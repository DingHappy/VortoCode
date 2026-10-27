"""Local iPhone development archive and evidence receipt; never uploads to Apple.

inspect reads public signing/profile metadata. archive defaults to unsigned;
--development uses an existing local profile and identity without provisioning updates.
verify checks the archived bundle, resource hashes and, when signed, entitlements.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ID = "com.vortocode.mobile"
APP_PATH = Path("VortoCodeMobile.xcarchive/Products/Applications/VortoCodeMobile.app")


def run(args, *, timeout=30):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        # Build/certificate tools can emit local metadata. Keep raw output private.
        raise ValueError(f"{Path(str(args[0])).name} failed (exit {result.returncode})")
    return result.stdout


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def matching_profile(data, identities, *, now=None):
    now = now or datetime.now(timezone.utc)
    entitlements = data.get("Entitlements", {})
    team = data.get("TeamIdentifier", [None])[0]
    expiration = data.get("ExpirationDate")
    if not (team and data.get("UUID") and isinstance(expiration, datetime) and utc(expiration) > now
            and entitlements.get("get-task-allow") is True and data.get("ProvisionedDevices")
            and fnmatch.fnmatchcase(f"{team}.{BUNDLE_ID}", entitlements.get("application-identifier", ""))):
        return None
    for certificate in data.get("DeveloperCertificates", []):
        fingerprint = hashlib.sha1(certificate).hexdigest().upper()  # Public certificate identifier, not a secret hash.
        if fingerprint in identities:
            return {"team": team, "uuid": data["UUID"], "identity": fingerprint}
    return None


def local_profiles():
    output = run(["security", "find-identity", "-v", "-p", "codesigning"]).decode()
    identities = set(re.findall(r'([A-F0-9]{40})\s+"(?:Apple Development|iPhone Developer):', output))
    profiles = {}
    for base in (Path.home() / "Library/MobileDevice/Provisioning Profiles",
                 Path.home() / "Library/Developer/Xcode/UserData/Provisioning Profiles"):
        for path in base.glob("*.mobileprovision"):
            try:
                data = plistlib.loads(run(["security", "cms", "-D", "-i", str(path)]))
                match = matching_profile(data, identities)
                if match:
                    profiles[match["uuid"]] = match
            except (ValueError, plistlib.InvalidFileException):
                continue
    return list(profiles.values())


def source_state():
    paths = run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                 "--", "mobile/ios", "scripts/build-ios-app.py"]).decode().split("\0")
    files = sorted({name for name in paths if name and not name.endswith(".md")})
    digest = hashlib.sha256()
    for name in files:
        path = ROOT / name
        if path.is_symlink():
            raise ValueError("Source input is a symlink")
        if not path.is_file():  # A tracked deletion is part of a dirty worktree.
            continue
        body = path.read_bytes()
        digest.update(name.encode() + b"\0" + str(len(body)).encode() + b"\0" + body)
    status = run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all", "--",
                  "mobile/ios", "scripts/build-ios-app.py"])
    return {"commit": run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).decode().strip(),
            "dirty": bool(status.strip()), "sha256": digest.hexdigest()}


def bundle_hashes(app):
    result = {}
    for path in sorted(app.rglob("*")):
        if path.is_symlink():
            raise ValueError("Bundle contains a symlink")
        if path.is_file():
            result[path.relative_to(app).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def validate_metadata(info):
    if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundlePackageType") != "APPL":
        raise ValueError("Unexpected app identity")
    if info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"] or info.get("DTPlatformName") != "iphoneos":
        raise ValueError("This is not an iPhone device bundle")
    if not re.fullmatch(r"\d+\.\d+\.\d+", info.get("CFBundleShortVersionString", "")):
        raise ValueError("Invalid release version")
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", info.get("CFBundleVersion", "")):
        raise ValueError("Invalid build number")
    ats = info.get("NSAppTransportSecurity", {})
    if any(ats.get(name) for name in ("NSAllowsArbitraryLoads", "NSAllowsArbitraryLoadsInWebContent", "NSExceptionDomains")):
        raise ValueError("Unexpected transport security exception")
    schemes = [scheme for item in info.get("CFBundleURLTypes", []) for scheme in item.get("CFBundleURLSchemes", [])]
    if schemes != ["vortocode-mobile-auth"]:
        raise ValueError("Unexpected authentication callback")


def inspect_bundle(app, development):
    info = plistlib.loads((app / "Info.plist").read_bytes())
    validate_metadata(info)
    executable = info.get("CFBundleExecutable", "")
    if Path(executable).name != executable or not executable:
        raise ValueError("Invalid executable name")
    if run(["lipo", "-archs", str(app / executable)]).decode().split() != ["arm64"]:
        raise ValueError("Unexpected device architecture")
    plistlib.loads((app / "PrivacyInfo.xcprivacy").read_bytes())
    if development:
        run(["codesign", "--verify", "--deep", "--strict", str(app)])
        entitlements = plistlib.loads(run(["codesign", "-d", "--entitlements", ":-", str(app)]))
        profile = plistlib.loads(run(["security", "cms", "-D", "-i", str(app / "embedded.mobileprovision")]))
        team = profile.get("TeamIdentifier", [None])[0]
        if not (team and entitlements.get("application-identifier") == f"{team}.{BUNDLE_ID}"
                and entitlements.get("com.apple.developer.team-identifier") == team
                and entitlements.get("get-task-allow") is True
                and profile.get("Entitlements", {}).get("get-task-allow") is True
                and fnmatch.fnmatchcase(f"{team}.{BUNDLE_ID}", profile.get("Entitlements", {}).get("application-identifier", ""))
                and profile.get("ProvisionedDevices") and utc(profile["ExpirationDate"]) > datetime.now(timezone.utc)):
            raise ValueError("Development signing/profile mismatch")
    elif (app / "embedded.mobileprovision").exists():
        raise ValueError("Unsigned archive unexpectedly contains a profile")
    return {"bundle_id": BUNDLE_ID, "version": info["CFBundleShortVersionString"], "build": info["CFBundleVersion"],
            "platform": "iphoneos", "architecture": "arm64", "signing": "development" if development else "unsigned"}


def verify(directory, *, current_source=False):
    receipt = json.loads((directory / "receipt.json").read_text())
    if receipt.get("schema_version") != 1 or receipt.get("app_path") != APP_PATH.as_posix():
        raise ValueError("Invalid delivery receipt")
    app = directory / APP_PATH
    if app.resolve() != app.absolute() or directory.is_symlink():
        raise ValueError("Archive path contains symlinks")
    if receipt.get("files") != bundle_hashes(app):
        raise ValueError("Archived app changed after the receipt was created")
    signing = receipt.get("bundle", {}).get("signing")
    if signing not in ("development", "unsigned"):
        raise ValueError("Unknown signing mode")
    if inspect_bundle(app, signing == "development") != receipt["bundle"]:
        raise ValueError("Bundle metadata does not match receipt")
    source_matches = source_state()["sha256"] == receipt["source"]["sha256"]
    if current_source and not source_matches:
        raise ValueError("Current source differs from the archived source")
    return {"verified": True, "source_matches": source_matches, "signing": signing,
            "device_installation_verified": False, "online_connection_verified": False}


def archive(directory, development, *, manual=False):
    if directory.exists():
        raise ValueError("Output already exists; choose a new delivery directory")
    profile = None
    if development:
        profiles = local_profiles()
        if len(profiles) != 1:
            raise ValueError("Development signing requires exactly one matching existing local profile")
        profile = profiles[0]
    source = source_state()
    directory.mkdir(parents=True)
    command = ["xcodebuild", "-project", str(ROOT / "mobile/ios/VortoCodeMobile.xcodeproj"), "-scheme", "VortoCodeMobile",
               "-configuration", "Release", "-destination", "generic/platform=iOS", "-disableAutomaticPackageResolution",
               "-archivePath", str(directory / "VortoCodeMobile.xcarchive"), "-derivedDataPath", str(directory / "derived"), "archive"]
    if profile:
        command += [f"CODE_SIGN_STYLE={'Manual' if manual else 'Automatic'}", f"DEVELOPMENT_TEAM={profile['team']}"]
        if manual:
            command += [f"PROVISIONING_PROFILE_SPECIFIER={profile['uuid']}", f"CODE_SIGN_IDENTITY={profile['identity']}"]
        else:
            command += ["PROVISIONING_PROFILE_SPECIFIER=", "CODE_SIGN_IDENTITY=Apple Development"]
    else:
        command += ["CODE_SIGNING_ALLOWED=NO"]
    with (directory / "build.log").open("wb") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise ValueError("Archive failed; inspect the private build.log")
    if source_state() != source:
        raise ValueError("Source changed during archive; no delivery receipt written")
    app = directory / APP_PATH
    if profile:
        embedded = plistlib.loads(run(["security", "cms", "-D", "-i", str(app / "embedded.mobileprovision")]))
        if embedded.get("UUID") != profile["uuid"]:
            raise ValueError("Archive selected a different provisioning profile")
    receipt = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "app_path": APP_PATH.as_posix(),
               "source": source, "bundle": inspect_bundle(app, development), "files": bundle_hashes(app),
               "device_installation_verified": False, "online_connection_verified": False,
               "app_store_release_verified": False}
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return verify(directory, current_source=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "archive", "verify"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--development", action="store_true", help="Development signing using existing local resources only")
    parser.add_argument("--manual-signing", action="store_true", help="For a manually managed profile; default supports Xcode-managed profiles")
    parser.add_argument("--require-current-source", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "inspect":
            result = {"matching_development_profile_count": len(local_profiles()), "source": source_state()}
        else:
            if args.output is None:
                parser.error("archive/verify requires --output")
            output = args.output.expanduser().resolve()
            result = archive(output, args.development, manual=args.manual_signing) if args.action == "archive" else verify(output, current_source=args.require_current_source)
        print(json.dumps(result))
    except (ValueError, OSError, subprocess.SubprocessError, plistlib.InvalidFileException, KeyError) as error:
        print(f"iOS delivery check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
