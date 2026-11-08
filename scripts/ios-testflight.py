"""把 iPhone App 签成 App Store 版、核对后上传 TestFlight（ADR-017 M4）。

和 scripts/build-ios-app.py 不同：那个只在本机做开发归档、从不联系 Apple；这个会用 App Store Connect
API 钥匙让 Xcode 自动建发布证书和描述文件，并上传。

    python scripts/ios-testflight.py check                  # 只核对前提（钥匙、团队、源码、图标），不构建
    python scripts/ios-testflight.py build --output DIR     # 归档 + 导出 ipa + 核对，不上传
    python scripts/ios-testflight.py upload --output DIR    # 重新核对 build 产出的那个 ipa，再上传

前提：
- 只从 main 发：工作区干净、当前在 main、HEAD 等于本地的 origin/main（先 git fetch）。
- App Store Connect API 钥匙：环境变量 ASC_KEY_ID、ASC_ISSUER_ID；钥匙文件放在
  ~/.appstoreconnect/private_keys/AuthKey_<ASC_KEY_ID>.p8，权限 600，不能在仓库里。
  脚本不打印、不记录钥匙内容和这两个 id。
- 团队：取本机 com.vortocode.mobile 开发描述文件里的团队（唯一），或用 IOS_TEAM_ID 指定。仓库是公开的，团队 id 不进提交。

ipa 上传前必须满足：推送是 production、不可调试（无 get-task-allow）、带 TestFlight 标记（beta-reports-active）、
主 App 和通知扩展的 bundle id 与团队对得上、描述文件不是开发 / Ad Hoc 的、声明了标准加密豁免。
回执写在 DIR/receipt.json；构建和上传的原始输出只写进 DIR 下的私有日志。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "mobile/ios/VortoCodeMobile.xcodeproj"
ICON = ROOT / "mobile/ios/VortoCodeMobile/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
BUNDLE_ID = "com.vortocode.mobile"
EXTENSION_ID = "com.vortocode.mobile.notification"
APP = "Payload/VortoCodeMobile.app"
EXTENSION = APP + "/PlugIns/VortoCodeNotification.appex"
KEY_DIR = Path.home() / ".appstoreconnect/private_keys"


class Refused(ValueError):
    """前提不满足或产物没通过核对；原因给人看，不含钥匙或团队 id。"""


# —— 前提 ——

def credentials(env=os.environ, key_dir: Path = KEY_DIR) -> dict:
    key_id = env.get("ASC_KEY_ID", "").strip()
    issuer = env.get("ASC_ISSUER_ID", "").strip()
    if not re.fullmatch(r"[A-Z0-9]{10}", key_id):
        raise Refused("没有设置 ASC_KEY_ID（App Store Connect API 钥匙 ID，10 位大写字母和数字）")
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", issuer):
        raise Refused("没有设置 ASC_ISSUER_ID（App Store Connect 的 Issuer ID）")
    path = key_dir / f"AuthKey_{key_id}.p8"
    if not path.is_file():
        raise Refused(f"找不到钥匙文件：{key_dir}/AuthKey_<ASC_KEY_ID>.p8")
    if path.stat().st_mode & 0o077:
        raise Refused("钥匙文件权限太宽，先 chmod 600")
    try:
        path.resolve().relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise Refused("钥匙文件不能放在仓库里")
    return {"key_id": key_id, "issuer": issuer, "path": path}


def team_from_profiles(profiles) -> str:
    """profiles：解码后的描述文件。只认 application-identifier 精确是 <团队>.com.vortocode.mobile 的。"""
    teams = set()
    for data in profiles:
        team = (data.get("TeamIdentifier") or [None])[0]
        if team and data.get("Entitlements", {}).get("application-identifier") == f"{team}.{BUNDLE_ID}":
            teams.add(team)
    if len(teams) != 1:
        raise Refused("本机没有唯一的 com.vortocode.mobile 描述文件可取团队；用 IOS_TEAM_ID 指定")
    return teams.pop()


def team_id(env=os.environ) -> str:
    explicit = env.get("IOS_TEAM_ID", "").strip()
    if explicit:
        if not re.fullmatch(r"[A-Z0-9]{10}", explicit):
            raise Refused("IOS_TEAM_ID 格式不对")
        return explicit
    profiles = []
    for base in (Path.home() / "Library/MobileDevice/Provisioning Profiles",
                 Path.home() / "Library/Developer/Xcode/UserData/Provisioning Profiles"):
        for path in base.glob("*.mobileprovision"):
            try:
                profiles.append(plistlib.loads(_run(["security", "cms", "-D", "-i", str(path)])))
            except (Refused, plistlib.InvalidFileException):
                continue
    return team_from_profiles(profiles)


def source_state() -> dict:
    status = _run(["git", "-C", str(ROOT), "status", "--porcelain"]).decode()
    branch = _run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"]).decode().strip()
    head = _run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).decode().strip()
    upstream = _run(["git", "-C", str(ROOT), "rev-parse", "origin/main"]).decode().strip()
    return {"clean": status == "", "branch": branch, "commit": head, "on_main": head == upstream}


def require_release_source(state: dict) -> str:
    if not state["clean"]:
        raise Refused("工作区有未提交的改动；只从干净的 main 发")
    if state["branch"] != "main" or not state["on_main"]:
        raise Refused("当前不是最新的 main（先 git fetch 再切到 main）；只从 main 发")
    return state["commit"]


def png_is_opaque(data: bytes) -> bool:
    """App Store 拒收带透明通道的图标：颜色类型只能是灰度（0）或 RGB（2），且没有 tRNS 块。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return False
    if data[25] not in (0, 2):
        return False
    offset = 8
    while offset + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        if kind == b"tRNS":
            return False
        if kind == b"IEND":
            break
        offset += 12 + length
    return True


def build_number(now: datetime | None = None) -> str:
    """每次上传要比上次大：用 UTC 时间到分钟。"""
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M")


def export_options(team: str) -> dict:
    return {"method": "app-store-connect", "destination": "export", "teamID": team, "signingStyle": "automatic",
            "uploadSymbols": True, "manageAppVersionAndBuildNumber": False, "testFlightInternalTestingOnly": False}


# —— 产物核对 ——

def check_signed(app_info: dict, app_entitlements: dict, ext_entitlements: dict, profile: dict, team: str) -> dict:
    """上传前的硬性条件；任何一项不对就拒绝。返回写进回执的摘要。"""
    def need(condition, reason):
        if not condition:
            raise Refused(reason)
    need(app_info.get("CFBundleIdentifier") == BUNDLE_ID, "主 App 的 bundle id 不对")
    need(re.fullmatch(r"\d+\.\d+\.\d+", app_info.get("CFBundleShortVersionString", "")), "版本号格式不对")
    need(re.fullmatch(r"\d{1,18}", app_info.get("CFBundleVersion", "")), "build 号格式不对")
    need(app_info.get("ITSAppUsesNonExemptEncryption") is False, "Info.plist 没声明标准加密豁免")
    need(app_entitlements.get("application-identifier") == f"{team}.{BUNDLE_ID}", "主 App 签名的团队或 bundle id 不对")
    need(ext_entitlements.get("application-identifier") == f"{team}.{EXTENSION_ID}", "通知扩展签名的团队或 bundle id 不对")
    need(app_entitlements.get("aps-environment") == "production", "推送不是 production 环境")
    for name, entitlements in (("主 App", app_entitlements), ("通知扩展", ext_entitlements)):
        need(not entitlements.get("get-task-allow"), f"{name}是可调试的开发签名")
        need(entitlements.get("beta-reports-active") is True, f"{name}没有 TestFlight 标记")
        groups = entitlements.get("keychain-access-groups") or []
        need(f"{team}.com.vortocode.mobile.shared" in groups, f"{name}缺少共享钥匙串分组（推送解密要用）")
    need(not profile.get("ProvisionedDevices") and not profile.get("ProvisionsAllDevices"), "描述文件是开发 / Ad Hoc 的，不是 App Store 的")
    need(profile.get("Entitlements", {}).get("aps-environment") == "production", "描述文件的推送不是 production")
    return {"bundle_id": BUNDLE_ID, "version": app_info["CFBundleShortVersionString"], "build": app_info["CFBundleVersion"],
            "aps_environment": "production", "debuggable": False, "testflight": True}


def inspect_ipa(ipa: Path, team: str) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        with zipfile.ZipFile(ipa) as archive:
            for member in archive.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    raise Refused("ipa 里有越界路径")
            archive.extractall(temp)
        app, extension = Path(temp) / APP, Path(temp) / EXTENSION
        if not (app / "Info.plist").is_file() or not (extension / "Info.plist").is_file():
            raise Refused("ipa 里缺主 App 或通知扩展")
        _run(["codesign", "--verify", "--deep", "--strict", str(app)])
        info = plistlib.loads((app / "Info.plist").read_bytes())
        app_entitlements = plistlib.loads(_run(["codesign", "-d", "--entitlements", ":-", str(app)]))
        ext_entitlements = plistlib.loads(_run(["codesign", "-d", "--entitlements", ":-", str(extension)]))
        profile = plistlib.loads(_run(["security", "cms", "-D", "-i", str(app / "embedded.mobileprovision")]))
        return check_signed(info, app_entitlements, ext_entitlements, profile, team)


# —— 动作 ——

def _run(args, *, timeout=60, env=None) -> bytes:
    result = subprocess.run(args, capture_output=True, timeout=timeout, env=env)
    if result.returncode:
        raise Refused(f"{Path(str(args[0])).name} 失败（退出码 {result.returncode}）")
    return result.stdout


def _logged(command, log: Path, *, timeout, env=None) -> None:
    with log.open("ab") as handle:
        os.chmod(log, 0o600)
        result = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout, env=env)
    if result.returncode:
        raise Refused(f"{Path(str(command[0])).name} 失败，原始输出在私有日志 {log.name}")


def _auth_flags(cred: dict) -> list:
    return ["-allowProvisioningUpdates", "-authenticationKeyPath", str(cred["path"]),
            "-authenticationKeyID", cred["key_id"], "-authenticationKeyIssuerID", cred["issuer"]]


def check() -> dict:
    credentials()
    team_id()
    commit = require_release_source(source_state())
    if not png_is_opaque(ICON.read_bytes()):
        raise Refused("App 图标带透明通道，App Store 会拒收")
    return {"ready": True, "commit": commit}


def build(output: Path) -> dict:
    if output.exists():
        raise Refused("输出目录已存在；换一个新目录")
    cred, team = credentials(), team_id()
    commit = require_release_source(source_state())
    if not png_is_opaque(ICON.read_bytes()):
        raise Refused("App 图标带透明通道，App Store 会拒收")
    output.mkdir(parents=True)
    os.chmod(output, 0o700)
    build_no = build_number()
    archive_path, log = output / "VortoCodeMobile.xcarchive", output / "build.log"
    _logged(["xcodebuild", "-project", str(PROJECT), "-scheme", "VortoCodeMobile", "-configuration", "Release",
             "-destination", "generic/platform=iOS", "-archivePath", str(archive_path),
             "-derivedDataPath", str(output / "derived"), "archive", *_auth_flags(cred),
             "CODE_SIGN_STYLE=Automatic", f"DEVELOPMENT_TEAM={team}", f"CURRENT_PROJECT_VERSION={build_no}"],
            log, timeout=1800)
    options = output / "ExportOptions.plist"
    options.write_bytes(plistlib.dumps(export_options(team)))
    _logged(["xcodebuild", "-exportArchive", "-archivePath", str(archive_path), "-exportOptionsPlist", str(options),
             "-exportPath", str(output / "export"), *_auth_flags(cred)], log, timeout=1800)
    ipas = list((output / "export").glob("*.ipa"))
    if len(ipas) != 1:
        raise Refused("导出结果里没有唯一的 ipa")
    if require_release_source(source_state()) != commit:
        raise Refused("构建期间源码变了；不写回执")
    summary = inspect_ipa(ipas[0], team)
    receipt = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "commit": commit,
               "ipa": ipas[0].relative_to(output).as_posix(), "ipa_sha256": hashlib.sha256(ipas[0].read_bytes()).hexdigest(),
               "bundle": summary, "uploaded": False, "testflight_processing_verified": False, "device_acceptance_verified": False}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    return receipt


def upload(output: Path) -> dict:
    receipt_path = output / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("schema_version") != 1 or receipt.get("uploaded"):
        raise Refused("回执无效，或这个 ipa 已经上传过（每次上传要新的 build 号，重新 build）")
    ipa = output / receipt["ipa"]
    if hashlib.sha256(ipa.read_bytes()).hexdigest() != receipt["ipa_sha256"]:
        raise Refused("ipa 在 build 之后被改过")
    cred, team = credentials(), team_id()
    if require_release_source(source_state()) != receipt["commit"]:
        raise Refused("当前 main 和构建时的提交不一致；重新 build")
    if inspect_ipa(ipa, team) != receipt["bundle"]:
        raise Refused("ipa 复核结果和回执不一致")
    env = {**os.environ, "API_PRIVATE_KEYS_DIR": str(cred["path"].parent)}
    _logged(["xcrun", "altool", "--upload-app", "--type", "ios", "--file", str(ipa),
             "--apiKey", cred["key_id"], "--apiIssuer", cred["issuer"]], output / "upload.log", timeout=3600, env=env)
    receipt.update(uploaded=True, uploaded_at=datetime.now(timezone.utc).isoformat())
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("check", "build", "upload"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "check":
            result = check()
        else:
            if args.output is None:
                parser.error("build / upload 需要 --output")
            output = args.output.expanduser().resolve()
            result = build(output) if args.action == "build" else upload(output)
        print(json.dumps(result, ensure_ascii=False))
    except (Refused, OSError, subprocess.SubprocessError, plistlib.InvalidFileException, KeyError, zipfile.BadZipFile) as error:
        print(f"TestFlight 发布没有继续：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
