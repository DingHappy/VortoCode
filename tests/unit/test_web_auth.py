"""Web 层 token 校验的行为测试：常时比较不改变放行/拒绝语义，且攻击者可控的
非 ASCII 输入不再引发异常（裸 hmac.compare_digest(str,str) 会抛 TypeError → 500）。

时序安全本身不可用单测断言（本就是统计侧信道）；这里钉的是「换成 compare_digest 后
行为一字不差 + 非 ASCII 兜底」，即改动的正确性边界。
"""
from src.web.auth import _ct_eq, _token_ok, ws_token_ok


class _FakeRequest:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


# ── _ct_eq：常时比较 + 非 ASCII 兜底 ──────────────────────────────────

def test_ct_eq_matches_and_differs():
    assert _ct_eq("s3cr3t-token", "s3cr3t-token") is True
    assert _ct_eq("s3cr3t-token", "s3cr3t-toker") is False
    assert _ct_eq("short", "a-much-longer-token") is False   # 长度不同亦安全


def test_ct_eq_non_ascii_input_does_not_raise():
    """攻击者可发含非 ASCII 的 header——必须只是「比较失败」，不得抛异常。"""
    assert _ct_eq("naïve-\U0001f511", "ascii-token") is False   # naïve-🔑
    assert _ct_eq("　￿", "ascii-token") is False            # 全角空格 + U+FFFF
    # 孤代理：surrogatepass 兜住不抛。运行时 chr 构造，避免源码字面量污染模块
    assert _ct_eq(chr(0xD800) + "x", "ascii-token") is False


# ── _token_ok / ws_token_ok：行为不变 ────────────────────────────────

def test_no_token_configured_allows(monkeypatch):
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    assert _token_ok(_FakeRequest()) is True   # 未配 token：本地开发放行


def test_bearer_header_accept_reject(monkeypatch):
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "the-secret")
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    assert _token_ok(_FakeRequest(headers={"authorization": "Bearer the-secret"})) is True
    assert _token_ok(_FakeRequest(headers={"authorization": "Bearer wrong"})) is False


def test_x_api_token_and_cookie(monkeypatch):
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "the-secret")
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    assert _token_ok(_FakeRequest(headers={"x-api-token": "the-secret"})) is True
    assert _token_ok(_FakeRequest(cookies={"vortocode_session": "the-secret"})) is True
    assert _token_ok(_FakeRequest()) is False   # 什么都没带：拒绝


def test_token_ok_non_ascii_header_no_500(monkeypatch):
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "the-secret")
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    # 不抛异常即通过（回归：曾用 == 时无碍，换 compare_digest 后靠 encode 兜底）
    assert _token_ok(_FakeRequest(headers={"x-api-token": "naïve-\U0001f511"})) is False


def test_ws_token_ok_parity(monkeypatch):
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "the-secret")
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    assert ws_token_ok(_FakeRequest(headers={"authorization": "Bearer the-secret"})) is True
    assert ws_token_ok(_FakeRequest(cookies={"vortocode_session": "the-secret"})) is True
    assert ws_token_ok(_FakeRequest(headers={"authorization": "Bearer nope"})) is False
