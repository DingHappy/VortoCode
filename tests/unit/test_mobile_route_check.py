"""发布检查的行为合同：无效身份、精确入口、有限探测及真实路由自身的拒绝响应。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.web.auth import MOBILE_SELF_AUTHENTICATED
from tests.unit.test_mobile_browser import setup  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mobile_route_check", ROOT / "scripts/mobile-route-check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def contract():
    return check.native_routes(ROOT / "src/web/auth.py")


def test_contract_tracks_every_native_route_and_template_exposes_them():
    routes = contract()
    assert set(routes) == {route for route in MOBILE_SELF_AUTHENTICATED if route[1].startswith(check.PREFIX)}
    check.check_caddy(ROOT / "deploy/Caddyfile.mobile-proxy", routes)
    assert ("GET", "/api/mobile/v1/snapshot") not in routes
    assert ("POST", "/api/mobile/pairing/start") not in routes


def test_new_native_route_is_automatically_checked_and_missing_ingress_is_detected(tmp_path):
    path = tmp_path / "auth.py"
    path.write_text("MOBILE_SELF_AUTHENTICATED = frozenset({('GET', '/api/mobile/native/future')})")
    routes = check.native_routes(path)
    assert routes == [("GET", "/api/mobile/native/future")]
    with pytest.raises(check.CheckFailed, match="future"):
        check.check_caddy(ROOT / "deploy/Caddyfile.mobile-proxy", routes)


@pytest.mark.parametrize("value", ["https://u:p@example.test", "http://example.test", "https://example.test/?x=y",
                                   "https://example.test/path", "https://example.test/#fragment", "https://example.test:bad"])
def test_public_origin_rejects_credentials_plain_http_and_extra_components(value):
    with pytest.raises(check.CheckFailed):
        check.validate_origin(value, public=True)


def test_environment_parser_never_executes_values(tmp_path):
    path = tmp_path / "env"
    path.write_text('VORTOONE_MOBILE_PUBLIC_ORIGIN="https://example.test:18443"\nSECRET=not-for-output\n')
    assert check.public_origin(path) == "https://example.test:18443"
    path.write_text('VORTOONE_MOBILE_PUBLIC_ORIGIN=$(touch /tmp/not-executed)\n')
    with pytest.raises(check.CheckFailed):
        check.public_origin(path)


@pytest.mark.parametrize("content", [
    "MOBILE_SELF_AUTHENTICATED = frozenset({('GET', '/api/mobile/native/*')})",
    "MOBILE_SELF_AUTHENTICATED = frozenset({('DELETE', '/api/mobile/native/x')})",
    "MOBILE_SELF_AUTHENTICATED = load_routes()",
    "MOBILE_SELF_AUTHENTICATED = frozenset({('GET', '/api/mobile/native/x?secret')})",
])
def test_malformed_or_dynamic_contract_is_refused(tmp_path, content):
    path = tmp_path / "auth.py"
    path.write_text(content)
    with pytest.raises(check.CheckFailed):
        check.native_routes(path)


def test_caddy_checker_rejects_prefix_match_and_wrong_method(tmp_path):
    path = tmp_path / "Caddyfile"
    for rule in ["method GET\n path /api/mobile/native/*", "method GET POST\n path /api/mobile/native/timeline"]:
        path.write_text("@mobile_get {\n" + rule + "\n}\n@mobile_post {\n method POST\n path /api/mobile/native/keys\n}\n")
        with pytest.raises(check.CheckFailed):
            check.check_caddy(path, [("GET", "/api/mobile/native/timeline")])


def test_real_routes_all_reject_the_probe_without_calling_account_services(setup, monkeypatch):  # noqa: F811
    client, _sessions, _now, calls, _root, _owner = setup
    for enabled in ("0", "1"):
        monkeypatch.setenv("VORTOONE_MOBILE_WRITES", enabled)
        for method, path in contract():
            response = client.request(method, path,
                headers={"Authorization": "Bearer " + check.INVALID_SESSION},
                **({"json": {}} if method == "POST" else {}))
            check.check_response(method, path, (response.status_code, response.json()))
    assert calls == []


def test_probe_is_bounded_and_never_follows_redirects_or_uses_real_keys(monkeypatch):
    calls = []

    def curl(args, **kwargs):
        calls.append(args)
        Path(args[args.index("--output") + 1]).write_text('{"detail":"登录或设备授权已失效，请重新登录"}')
        return SimpleNamespace(returncode=0, stdout="401")

    monkeypatch.setattr(check.subprocess, "run", curl)
    result = check.request("http://127.0.0.1:18081", "GET", "/api/mobile/native/timeline", host_origin="https://example.test")
    assert result[0] == 401 and len(calls) == 1
    command = calls[0]
    assert command[1] == "--disable"  # 不继承 .curlrc 中的真实 Cookie、认证或不安全 TLS 选项
    assert "--location" not in command and "-L" not in command and "--insecure" not in command
    assert "--retry" not in command
    assert command[command.index("--max-time") + 1] == "8"
    assert command[command.index("--max-filesize") + 1] == "65536"
    assert "Authorization: Bearer " + check.INVALID_SESSION in command
    assert "Host: example.test" in command
    assert "X-Forwarded-Proto: https" in command


@pytest.mark.parametrize("response", [(200, {}), (301, {}), (401, {"detail": "Unauthorized"}),
                                      (404, None), (429, {'detail': 'rate limited'}), (500, {"detail": "crash"})])
def test_wrong_layer_redirect_or_success_is_not_a_pass(response):
    with pytest.raises(check.CheckFailed):
        check.check_response("GET", "/api/mobile/native/timeline", response)


def test_certificate_failure_is_not_retried_or_reported_as_empty(monkeypatch):
    calls = []
    def curl(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=60, stdout="000")
    monkeypatch.setattr(check.subprocess, "run", curl)
    with pytest.raises(check.CheckFailed):
        check.request("https://example.test", "GET", "/api/mobile/native/timeline")
    assert len(calls) == 1


@pytest.mark.parametrize("code", [7, 28, 52, 56])
def test_transient_connection_failures_are_retried_a_bounded_number_of_times(monkeypatch, code):
    calls = []

    def flaky(args, **kwargs):
        calls.append(args)
        if len(calls) < 3:
            return SimpleNamespace(returncode=code, stdout="000")
        Path(args[args.index("--output") + 1]).write_text('{"detail":"登录或设备授权已失效，请重新登录"}')
        return SimpleNamespace(returncode=0, stdout="401")

    monkeypatch.setattr(check.subprocess, "run", flaky)
    monkeypatch.setattr(check.time, "sleep", lambda _seconds: None)
    assert check.request("https://example.test", "GET", "/api/mobile/native/timeline")[0] == 401
    assert len(calls) == 3                                   # 失败两次，第三次成功

    calls.clear()
    monkeypatch.setattr(check.subprocess, "run", lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=code, stdout="000"))
    with pytest.raises(check.CheckFailed, match="重试 2 次"):
        check.request("https://example.test", "GET", "/api/mobile/native/timeline")
    assert len(calls) == 3                                   # 最多三次，不会无限重试


def test_environment_written_before_the_rename_is_still_read(tmp_path):
    """改名前写的环境文件用旧键名（ADR-018 批次 B）；两个都有时以新键名为准。"""
    path = tmp_path / "env"
    path.write_text("VORTOCODE_MOBILE_PUBLIC_ORIGIN=https://old.example.test:18443\n")
    assert check.public_origin(path) == "https://old.example.test:18443"
    path.write_text("VORTOCODE_MOBILE_PUBLIC_ORIGIN=https://old.example.test:18443\n"
                    "VORTOONE_MOBILE_PUBLIC_ORIGIN=https://new.example.test:18443\n")
    assert check.public_origin(path) == "https://new.example.test:18443"


def test_release_probe_respects_failure_backoff_when_origins_share_a_source(monkeypatch):
    from src.web.mobile_rate_limit import MobileRateLimiter
    now, waits, responses = [0.0], [], []
    limiter = MobileRateLimiter(clock=lambda: now[0])
    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds
    def request_once(origin, method, path, **kwargs):
        if (method, path) in check.CLOSED_ROUTES:
            return 404, None
        blocked = limiter.admit('one-source')
        if blocked:
            response = (429, {'detail': 'rate limited'})
        else:
            limiter.authentication_failed('one-source')
            response = (401, {'detail': '登录或设备授权已失效，请重新登录'})
        responses.append(response[0])
        return response
    monkeypatch.setattr(check, '_request_once', request_once)
    monkeypatch.setattr(check.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(check.time, 'sleep', sleep)
    routes = [('GET', check.PREFIX + 'timeline')] * 8
    check.probe(routes, 'http://127.0.0.1:18081', 'https://example.test')
    assert responses == [401] * 16
    assert waits and sum(waits) <= 4 * 61


@pytest.mark.parametrize('limits', [
    'WINDOW_SECONDS=60\nREQUEST_LIMIT=120\nFAILURE_LIMIT=1\n',
    'WINDOW_SECONDS=True\nREQUEST_LIMIT=120\nFAILURE_LIMIT=5\n',
    'WINDOW_SECONDS=60\nREQUEST_LIMIT=0\nFAILURE_LIMIT=5\n',
    'WINDOW_SECONDS=60\nREQUEST_LIMIT=120\nFAILURE_LIMIT=int("5")\n',
    'WINDOW_SECONDS=60\nREQUEST_LIMIT=120\nFAILURE_LIMIT=5\nFAILURE_LIMIT=9\n',
])
def test_unverifiable_limiter_policy_fails_before_any_probe(tmp_path, monkeypatch, limits):
    auth = tmp_path / 'auth.py'
    auth.write_text("MOBILE_SELF_AUTHENTICATED=frozenset({('GET','/api/mobile/native/timeline')})")
    auth.with_name('mobile_rate_limit.py').write_text(limits)
    env = tmp_path / 'environment'
    env.write_text('VORTOONE_MOBILE_PUBLIC_ORIGIN=https://example.test\n')
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid policy must not send requests')
    monkeypatch.setattr(check, 'request', forbidden)
    assert check.main(['--auth-file', str(auth), '--environment-file', str(env)]) == 1


def test_probe_pacing_counts_transport_retries_with_virtual_time(monkeypatch):
    now, calls, outcomes = [0.0], [], [56, 56, 0]
    def sleep(seconds): now[0] += seconds
    def curl(args, **kwargs):
        calls.append(now[0])
        code = outcomes.pop(0)
        if not code:
            Path(args[args.index('--output') + 1]).write_text('{"detail":"登录或设备授权已失效，请重新登录"}')
        return SimpleNamespace(returncode=code, stdout='000' if code else '401')
    monkeypatch.setattr(check.subprocess, 'run', curl)
    monkeypatch.setattr(check.time, 'sleep', sleep)
    pacer = check.ProbePacer((60, 1), clock=lambda: now[0], sleep=sleep)
    assert check.request('https://example.test', 'GET', check.PREFIX + 'timeline', pacer=pacer)[0] == 401
    assert len(calls) == 3 and calls[1] - calls[0] >= 61 and calls[2] - calls[1] >= 61


def test_probe_policy_uses_the_lower_request_and_failure_budget(tmp_path):
    auth = tmp_path / 'auth.py'
    auth.with_name('mobile_rate_limit.py').write_text('WINDOW_SECONDS=37\nREQUEST_LIMIT=2\nFAILURE_LIMIT=9\n')
    assert check.limiter_policy(auth) == (37, 2)
