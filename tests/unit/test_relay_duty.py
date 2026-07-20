"""中转站值班巡检（B8-① 试点一）的行为测试。

全部用注入的假 fetch + 假 env + 假时钟，零网络零真实凭据；断报告内容与退出码，不查源码。
"""
import json

from src.gateway.relay_duty import DEFAULT_RELAY_URL, run_duty

BASE = DEFAULT_RELAY_URL


def _fake_fetch(routes, calls=None):
    """按 URL 前缀路由的假中转站。routes: {url片段: (status, body_dict|str)}。"""
    def fetch(url, headers):
        if calls is not None:
            calls.append((url, dict(headers)))
        for fragment, (status, body) in routes.items():
            if fragment in url:
                return status, body if isinstance(body, str) else json.dumps(body)
        raise AssertionError(f"未预期的请求：{url}")
    return fetch


def _healthy_routes(start_time=1700000000):
    return {
        "/api/status": (200, {"success": True, "data": {"version": "v0.6.10", "start_time": start_time}}),
        "/api/pricing": (200, {"data": [{"model": "mimo-v2.5"}, {"model": "gpt-4o"}]}),
    }


def test_all_green_without_credentials_skips_authed_checks(tmp_path):
    """免鉴权两项过 + 三项未配置跳过 → 退出码 0，结论明说「跳过 ≠ 通过」。"""
    code, report = run_duty(str(tmp_path), fetch=_fake_fetch(_healthy_routes()), env={}, now=1700003600)
    assert code == 0
    assert "✓ 存活 /api/status" in report and "无重启" in report
    assert "✓ 价目 /api/pricing" in report and "2 项" in report
    assert report.count("- ") == 3 and "跳过（未配" in report          # 三项未配置：计费/渠道池/日用量
    assert "跳过 ≠ 通过" in report


def test_status_down_is_anomaly_with_evidence(tmp_path):
    routes = dict(_healthy_routes())
    routes["/api/status"] = (502, "Bad Gateway")
    code, report = run_duty(str(tmp_path), fetch=_fake_fetch(routes), env={}, now=1700003600)
    assert code == 1
    assert "✗ 存活 /api/status" in report and "502" in report
    assert "1 项异常" in report


def test_unreachable_relay_is_anomaly_not_crash(tmp_path):
    """连接失败/超时是巡检要报告的事实，不是巡检自己崩掉的理由。"""
    def fetch(url, headers):
        raise TimeoutError("connect timeout")
    code, report = run_duty(str(tmp_path), fetch=fetch, env={}, now=1700003600)
    assert code == 1
    assert "TimeoutError" in report


def test_restart_detected_via_state_snapshot_and_self_clears(tmp_path):
    """start_time 变化 → 报异常；再跑一夜（start_time 未再变）→ 自动转绿。"""
    env = {}
    code1, _ = run_duty(str(tmp_path), fetch=_fake_fetch(_healthy_routes(1700000000)), env=env, now=1700003600)
    assert code1 == 0                                                  # 首跑建立基线

    code2, report2 = run_duty(str(tmp_path), fetch=_fake_fetch(_healthy_routes(1700090000)), env=env, now=1700090000)
    assert code2 == 1                                                  # 重启被抓到
    assert "进程重启过" in report2

    code3, report3 = run_duty(str(tmp_path), fetch=_fake_fetch(_healthy_routes(1700090000)), env=env, now=1700176400)
    assert code3 == 0 and "无重启" in report3                          # 基线已更新，自动转绿


def test_channel_cooling_is_anomaly_with_reason(tmp_path):
    routes = dict(_healthy_routes())
    routes["/api/channel_pool"] = (200, {"data": {
        "cooling_count": 1,
        "channels": [
            {"id": 1, "name": "openai-main", "status": 1},
            {"id": 2, "name": "backup", "status": 2, "cool_reason": "upstream 429"},
        ],
    }})
    routes["/api/log/stat"] = (200, {"data": {"quota": 120}})
    env = {"VORTOCODE_RELAY_ADMIN_TOKEN": "admin-token"}
    code, report = run_duty(str(tmp_path), fetch=_fake_fetch(routes), env=env, now=1700003600)
    assert code == 1
    assert "✗ 渠道池" in report and "backup: upstream 429" in report
    assert "✓ 当日消耗" in report and "quota=120" in report


def test_daily_quota_over_limit_is_anomaly(tmp_path):
    routes = dict(_healthy_routes())
    routes["/api/channel_pool"] = (200, {"data": {"cooling_count": 0, "channels": [{"id": 1, "status": 1}]}})
    routes["/api/log/stat"] = (200, {"data": {"quota": 999999}})
    env = {"VORTOCODE_RELAY_ADMIN_TOKEN": "admin-token",
           "VORTOCODE_RELAY_DAILY_QUOTA_LIMIT": "500000"}
    code, report = run_duty(str(tmp_path), fetch=_fake_fetch(routes), env=env, now=1700003600)
    assert code == 1
    assert "超过日阈值" in report


def test_credentials_go_to_right_headers_and_never_into_report(tmp_path):
    """sk 走 Bearer、管理 token 走 Authorization——且报告里绝不回显任何凭据。"""
    calls = []
    routes = dict(_healthy_routes())
    routes["/dashboard/billing/usage"] = (200, {"object": "list", "total_usage": 4200})
    routes["/api/channel_pool"] = (200, {"data": {"cooling_count": 0, "channels": [{"id": 1, "status": 1}]}})
    routes["/api/log/stat"] = (200, {"data": {"quota": 7}})
    env = {"VORTOCODE_RELAY_SK": "sk-secret-user-key",
           "VORTOCODE_RELAY_ADMIN_TOKEN": "secret-admin-token"}
    code, report = run_duty(str(tmp_path), fetch=_fake_fetch(routes, calls), env=env, now=1700003600)
    assert code == 0
    billing = next(h for u, h in calls if "/dashboard/billing/usage" in u)
    assert billing["Authorization"] == "Bearer sk-secret-user-key"
    pool = next(h for u, h in calls if "/api/channel_pool" in u)
    assert pool["Authorization"] == "secret-admin-token"
    assert "secret" not in report                                     # 凭据永不进报告/台账


def test_custom_relay_url_from_env(tmp_path):
    calls = []
    env = {"VORTOCODE_RELAY_URL": "http://127.0.0.1:3001/"}
    routes = {
        "/api/status": (200, {"success": True, "data": {"version": "dev", "start_time": 1}}),
        "/api/pricing": (200, {"data": [{"model": "m"}]}),
    }
    code, report = run_duty(str(tmp_path), fetch=_fake_fetch(routes, calls), env=env, now=1700003600)
    assert code == 0
    assert all(url.startswith("http://127.0.0.1:3001/") for url, _ in calls)
    assert "http://127.0.0.1:3001" in report
