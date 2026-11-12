#!/usr/bin/env python3
"""手机发布的只读路由检查：从目标快照读取合同，校验精确入口并探测本机/公网。

只发无效会话和空请求体；不使用真实设备、管理员令牌、Cookie、模型或签名钥匙。
不修改 Caddy；低频探测，不跟随重定向，TLS 与 HTTP 错误不重试。
"""
from __future__ import annotations

import argparse
import ast
from collections import deque
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

PREFIX = "/api/mobile/native/"
INVALID_SESSION = "vcn_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CLOSED_ROUTES = (("GET", "/api/assistant/timeline"), ("POST", "/api/assistant/app-relay/timeline"),
                 ("GET", "/api/assistant/access"), ("POST", "/api/assistant/app-relay/access"))


class CheckFailed(ValueError):
    pass


def limiter_policy(auth_file: Path) -> tuple[int, int]:
    """读取目标快照的静态限频参数，不导入 Web 或改变服务器策略。"""
    names = {'WINDOW_SECONDS', 'REQUEST_LIMIT', 'FAILURE_LIMIT'}
    values = {}
    try:
        tree = ast.parse(auth_file.with_name('mobile_rate_limit.py').read_text(encoding='utf-8'))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    if target.id in values:
                        raise ValueError('duplicate limit')
                    value = ast.literal_eval(node.value)
                    if type(value) is not int:
                        raise ValueError('non-integer limit')
                    values[target.id] = value
        if (set(values) != names or values['WINDOW_SECONDS'] <= 0
                or values['REQUEST_LIMIT'] <= 0 or values['FAILURE_LIMIT'] < 2):
            raise ValueError('invalid limits')
    except (OSError, SyntaxError, ValueError, TypeError) as error:
        raise CheckFailed('目标快照的限频参数无法核对；未发探测请求') from error
    return values['WINDOW_SECONDS'], min(values['REQUEST_LIMIT'], values['FAILURE_LIMIT'] - 1)


class ProbePacer:
    """所有入口共享探测预算；重试也计入，避免来源相同时自触失败退避。"""

    def __init__(self, policy=None, *, clock=None, sleep=None):
        window, self.limit = policy or limiter_policy(Path(__file__).resolve().parents[1] / 'src/web/auth.py')
        self.window = window + 1  # 留出窗口边界余量；不改变网关本身的阈值。
        self.clock, self.sleep = clock or time.monotonic, sleep or time.sleep
        self.completed = deque()

    def wait(self):
        now = self.clock()
        while self.completed and self.completed[0] <= now - self.window:
            self.completed.popleft()
        if len(self.completed) >= self.limit:
            print('验收探测等待限频窗口', flush=True)
            self.sleep(max(0, self.completed[0] + self.window - now))
            now = self.clock()
            while self.completed and self.completed[0] <= now - self.window:
                self.completed.popleft()

    def finish(self):
        self.completed.append(self.clock())


def native_routes(auth_file: Path) -> list[tuple[str, str]]:
    """只解析 literal，不 import Web、执行配置或加载模型。新增接口自动进入检查。"""
    tree = ast.parse(auth_file.read_text(encoding="utf-8"))
    matches = [node.value for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "MOBILE_SELF_AUTHENTICATED" for t in node.targets)]
    if len(matches) != 1:
        raise CheckFailed("无法读取目标快照的手机路由合同")
    value = matches[0]
    if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset" and len(value.args) == 1 and not value.keywords):
        raise CheckFailed("手机路由合同需要显式 frozenset literal")
    try:
        routes = ast.literal_eval(value.args[0])
    except (ValueError, TypeError) as error:
        raise CheckFailed("手机路由合同不是 literal") from error
    if not isinstance(routes, (set, list, tuple)):
        raise CheckFailed("手机路由合同需要条目集合")
    out = []
    for entry in routes:
        if not isinstance(entry, tuple) or len(entry) != 2 or not all(isinstance(s, str) for s in entry):
            raise CheckFailed("手机路由合同含不合法条目")
        method, path = entry
        if not path.startswith(PREFIX):
            continue
        if method not in ("GET", "POST") or not re.fullmatch(r"/api/mobile/native/[a-z0-9/-]+", path):
            raise CheckFailed("原生手机路由需要精确 GET/POST 路径")
        out.append(entry)
    if not out:
        raise CheckFailed("目标快照没有原生手机路由")
    return sorted(set(out))


def public_origin(environment: Path) -> str:
    # 新键名优先；改名前写的环境文件里是旧键名 VORTOCODE_MOBILE_PUBLIC_ORIGIN（ADR-018 批次 B），网关同样认它。
    found: dict[str, list[str]] = {"VORTOONE": [], "VORTOCODE": []}
    for line in environment.read_text(encoding="utf-8").splitlines():
        prefix = next((p for p in found if line.startswith(f"{p}_MOBILE_PUBLIC_ORIGIN=")), None)
        if prefix:
            parsed = shlex.split(line.split("=", 1)[1], comments=True)
            if len(parsed) != 1:
                raise CheckFailed("手机公网入口配置无效")
            found[prefix].append(parsed[0])
    values = found["VORTOONE"] or found["VORTOCODE"]
    if len(values) != 1:
        raise CheckFailed("环境文件需配置唯一 VORTOONE_MOBILE_PUBLIC_ORIGIN")
    return validate_origin(values[0], public=True)


def validate_origin(value: str, *, public: bool) -> str:
    parts = urlsplit(value)
    try:
        parts.port
    except ValueError as error:
        raise CheckFailed("入口端口无效") from error
    if (not parts.hostname or parts.username is not None or parts.password is not None
            or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise CheckFailed("入口必须是不含凭据、路径或参数的 origin")
    if public:
        if parts.scheme != "https":
            raise CheckFailed("公网检查必须使用 HTTPS")
    elif parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise CheckFailed("本机检查只允许 HTTP 回环地址")
    return value.rstrip("/")


def check_caddy(path: Path, routes: list[tuple[str, str]]) -> None:
    """检查部署约定的两个精确 matcher；不替代 Caddy 自身的完整 validate。"""
    content = path.read_text(encoding="utf-8")
    for method, name in (("GET", "mobile_get"), ("POST", "mobile_post")):
        blocks = re.findall(r"^[ \t]*@" + name + r"\s*\{([^{}]*)\}", content, re.M)
        if len(blocks) != 1:
            raise CheckFailed(f"Caddy 需要唯一 @{name} matcher")
        methods = re.findall(r"^[ \t]*method[ \t]+([^\n]+)", blocks[0], re.M)
        paths = re.findall(r"^[ \t]*path[ \t]+([^\n]+)", blocks[0], re.M)
        if methods != [method] or len(paths) != 1:
            raise CheckFailed(f"Caddy @{name} 需要精确方法与 path 列表")
        allowed = set(paths[0].split())
        if any("*" in p or not p.startswith(("/api/mobile/", "/mobile")) for p in allowed):
            raise CheckFailed(f"Caddy @{name} 含前缀通配或非手机路径")
        missing = [p for m, p in routes if m == method and p not in allowed]
        if missing:
            raise CheckFailed("HTTPS 白名单缺少：" + ", ".join(f"{method} {p}" for p in missing))


# 只有这几种是一时的网络问题，重试最多 2 次：连不上（7）、连接超时（28）、空响应（52）、读到一半断开（56）。
# 证书 / TLS 错误、HTTP 状态不对、响应过大都是真问题，照旧一次就失败。常驻电脑从内网访问自己的公网入口要绕路由器，
# 偶尔会超时（2026-11-08 实测 20 次里 4 次连接超时），一次超时不该让整次发布回滚。
_TRANSIENT_CURL_EXITS = {7, 28, 52, 56}
_ATTEMPTS = 3


def request(origin: str, method: str, path: str, *, host_origin: str | None = None,
            pacer: ProbePacer | None = None) -> tuple[int, object]:
    """curl 只用合成无效身份；错误正文/URI/真实配置不会被打印。"""
    for attempt in range(_ATTEMPTS):
        if pacer is not None:
            pacer.wait()
        try:
            return _request_once(origin, method, path, host_origin=host_origin)
        except _Transient:
            if attempt == _ATTEMPTS - 1:
                raise CheckFailed(f"{method} {path}：连接/TLS/响应大小检查失败（重试 {_ATTEMPTS - 1} 次仍连不上）") from None
            time.sleep(1)
        finally:
            if pacer is not None:
                pacer.finish()
    raise AssertionError("unreachable")


class _Transient(Exception):
    """一时的网络问题（见 _TRANSIENT_CURL_EXITS）。"""


def _request_once(origin: str, method: str, path: str, *, host_origin: str | None = None) -> tuple[int, object]:
    with tempfile.TemporaryDirectory(prefix="vc-route-probe-") as directory:
        body = Path(directory) / "body"
        command = ["curl", "--disable", "--silent", "--show-error", "--connect-timeout", "3", "--max-time", "8",
                   "--max-filesize", "65536", "--request", method, "--header", "Authorization: Bearer " + INVALID_SESSION,
                   "--header", "Content-Type: application/json", "--output", str(body), "--write-out", "%{http_code}"]
        if host_origin:
            command.extend(["--header", "Host: " + urlsplit(host_origin).netloc,
                            "--header", "X-Forwarded-Proto: https"])
        if method == "POST":
            command.extend(["--data", "{}"])
        command.append(origin + path)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CheckFailed(f"{method} {path}：连接失败") from error
        if result.returncode in _TRANSIENT_CURL_EXITS:
            raise _Transient()
        if result.returncode != 0 or not re.fullmatch(r"[0-9]{3}", result.stdout):
            raise CheckFailed(f"{method} {path}：连接/TLS/响应大小检查失败")
        raw = body.read_bytes() if body.exists() else b""
        if len(raw) > 65536:
            raise CheckFailed(f"{method} {path}：响应过大")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            data = None
        return int(result.stdout), data


def check_response(method: str, path: str, response: tuple[int, object]) -> None:
    status, data = response
    detail = data.get("detail") if isinstance(data, dict) else None
    if (method, path) == ("POST", PREFIX + "exchange"):
        valid = status == 422 and isinstance(detail, list) and bool(detail)
    else:
        valid = ((status == 401 and detail == "登录或设备授权已失效，请重新登录")
                 or (status == 403 and detail == "这台助理没有开启手机写入"))
    if not valid:
        raise CheckFailed(f"{method} {path}：未到达预期路由鉴权（HTTP {status}）")


def probe(routes: list[tuple[str, str]], loopback: str, public: str, *, pacer=None) -> None:
    pacer = pacer or ProbePacer()
    # 每个接口一次本机、一次公网；HTTP 错误仍直接失败，不把 429 当路由正常。
    for method, path in routes:
        local = request(loopback, method, path, host_origin=public, pacer=pacer)
        check_response(method, path, local)
        remote = request(public, method, path, pacer=pacer)
        check_response(method, path, remote)
        if remote != local:
            raise CheckFailed(f"{method} {path}：公网与本机路由响应不一致")
        print(f"✓ {method} {path}：本机/公网路由鉴权一致", flush=True)
    for method, path in CLOSED_ROUTES:
        status, _ = request(public, method, path, pacer=pacer)
        if status != 404:
            raise CheckFailed(f"{method} {path}：管理员/relay 入口未在公网关闭（HTTP {status}）")
    print(f"✓ {len(routes)} 个手机接口与 {len(CLOSED_ROUTES)} 个非公开入口检查通过", flush=True)


def main(argv=None, *, clock=None, sleep=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-file", required=True, type=Path)
    parser.add_argument("--environment-file", required=True, type=Path)
    parser.add_argument("--caddyfile", type=Path)
    parser.add_argument("--loopback-origin", default="http://127.0.0.1:18081")
    parser.add_argument("--contract-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        routes = native_routes(args.auth_file)
        origin = public_origin(args.environment_file)
        loopback = validate_origin(args.loopback_origin, public=False)
        policy = limiter_policy(args.auth_file)
        if args.caddyfile:
            check_caddy(args.caddyfile, routes)
        if args.contract_only:
            print(f"✓ {len(routes)} 个原生手机接口合同已核对（未发请求、未修改入口）")
        else:
            probe(routes, loopback, origin, pacer=ProbePacer(policy, clock=clock, sleep=sleep))
    except (CheckFailed, OSError, SyntaxError, ValueError) as error:
        # 只打印合同/路径诊断；配置文件/响应内容从不进日志。
        message = str(error) if isinstance(error, CheckFailed) else type(error).__name__
        print("✗ 手机路由检查失败：" + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
