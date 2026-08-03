"""WS 层心跳开关的回归网。

真机实测（2026-08-03，两个独立窗口 578s / 374s）：钉钉**不发应用层 ping**，
`last_frame_age` 全程 null。所以"桥连着"目前是弱信号——协议层 PING/PONG 被 aiohttp
的 autoping 吃在内部。heartbeat=N 让 aiohttp 主动 ping 并在无 pong 时关连接，
"连接还开着"就变成了硬证据。

**默认关**是这条的核心：真机上万一钉钉对客户端 ping 反应不佳，最坏是反复重连，
而那是活的生产 bot。所以锁在配置门后（同卡片模板/深链）。
"""

import pytest

from src.im.dingtalk import _ws_heartbeat_kw


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("VORTOCODE_DD_WS_HEARTBEAT", raising=False)


def test_unset_passes_nothing():
    """不设 → 不传任何参数，ws_connect 调用与今天**逐字节相同**。"""
    assert _ws_heartbeat_kw() == {}


def test_configured_passes_heartbeat():
    import os
    os.environ["VORTOCODE_DD_WS_HEARTBEAT"] = "30"
    try:
        assert _ws_heartbeat_kw() == {"heartbeat": 30.0}
    finally:
        os.environ.pop("VORTOCODE_DD_WS_HEARTBEAT", None)


@pytest.mark.parametrize("bad", ["", "0", "-5", "abc", "   "])
def test_bad_values_fall_back_to_off(monkeypatch, bad):
    """写错/关闭一律回落到"不传"——**绝不能因为这个配置让桥建不起来**。"""
    monkeypatch.setenv("VORTOCODE_DD_WS_HEARTBEAT", bad)
    assert _ws_heartbeat_kw() == {}


class _FakeResp:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return {"endpoint": "wss://fake/connect", "ticket": "tkt"}


class _FakeSession:
    """假 aiohttp 会话：记下 ws_connect 实际收到的关键字参数。"""

    def __init__(self):
        self.ws_kwargs = None

    def post(self, *a, **kw):
        return _FakeResp()

    async def ws_connect(self, url, **kw):
        self.ws_kwargs = kw
        return object()          # _AioWS 只是包一层，不碰它的方法


async def _connect_with(monkeypatch, env_value):
    """跑真正的 _default_connect（注入假会话），返回它传给 ws_connect 的 kwargs。

    刻意不用 inspect.getsource 查子串——那种测试在它声称要防的回归里会保持绿色
    （CLAUDE.md 明令禁止，本仓清理过一轮）。这里断的是**真实调用参数**。
    """
    from src.im.dingtalk import DingTalkAdapter

    if env_value is None:
        monkeypatch.delenv("VORTOCODE_DD_WS_HEARTBEAT", raising=False)
    else:
        monkeypatch.setenv("VORTOCODE_DD_WS_HEARTBEAT", env_value)
    adapter = DingTalkAdapter("cid", "secret", "owner")
    session = _FakeSession()
    adapter._session = session
    await adapter._default_connect()
    return session.ws_kwargs


@pytest.mark.asyncio
async def test_connect_omits_heartbeat_when_unset(monkeypatch):
    """没配 → ws_connect 收不到 heartbeat，建连参数与今天完全一致。"""
    assert await _connect_with(monkeypatch, None) == {}


@pytest.mark.asyncio
async def test_connect_passes_heartbeat_when_configured(monkeypatch):
    """配了 → 真的传到 ws_connect（函数写对了但没接上等于白写）。"""
    assert await _connect_with(monkeypatch, "30") == {"heartbeat": 30.0}
