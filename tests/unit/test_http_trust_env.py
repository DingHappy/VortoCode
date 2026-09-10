"""出网会话必须吃 `HTTP(S)_PROXY`，本地会话必须不吃。

`aiohttp.ClientSession()` 默认 **不读** `HTTP_PROXY`/`HTTPS_PROXY`（requests/httpx/openai SDK
都读，所以很容易以为它也读）。在"纯环境变量代理、没有 TUN"的机器上，这意味着一切出网调用
直接失败——而报错是超时，不是"没配代理"，人会往网络故障上排查。

真机 2026-09-10：生产机 192.168.10.97 正是这种机型。钉钉一直能用只是因为 api.dingtalk.com
在国内直连就通，坑被掩盖了几个月；同一台机器上 `curl` 走代理 302、aiohttp 直连 8 秒超时。

**断的是会话对象的真实属性，不是源码里有没有那串字符**（仓库明令禁止 inspect.getsource
查子串那种安慰剂测试）。
"""
import aiohttp
import pytest

from src.utils.http import local_session, outbound_session


@pytest.mark.asyncio
async def test_outbound_reads_the_proxy_environment():
    async with outbound_session() as s:
        assert s.trust_env is True


@pytest.mark.asyncio
async def test_local_never_reads_the_proxy_environment():
    """NO_PROXY 没配全是常态，而把 127.0.0.1 推进代理的后果是"服务明明在跑却报不可达"。"""
    async with local_session() as s:
        assert s.trust_env is False


@pytest.mark.asyncio
async def test_the_factories_pass_other_options_through():
    timeout = aiohttp.ClientTimeout(total=3)
    async with outbound_session(timeout=timeout, headers={"X-A": "1"}) as s:
        assert s.timeout.total == 3 and s.headers.get("X-A") == "1"


@pytest.mark.asyncio
async def test_an_explicit_choice_still_wins():
    """工厂给的是默认值不是强制值——极少数调用点要反着来时，别逼人绕开工厂自己 new。"""
    async with outbound_session(trust_env=False) as s:
        assert s.trust_env is False


# ------------------------------------------------------------------ 真正的调用点
@pytest.mark.asyncio
async def test_telegram_builds_its_session_through_the_outbound_factory(monkeypatch, tmp_path):
    """**这条是本次改动的直接目标**：97 上接 Telegram 时，直连必然 8 秒超时。

    驱动真实代码路径（`_default_download`），断它拿到的会话是从哪个工厂来的——
    不是去源码里找字符串。
    """
    from src.im import telegram as tg

    made = []

    class _Resp:
        status = 200

        async def read(self):
            return b"ok"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeSession:
        trust_env = True

        def __init__(self):
            made.append(self)

        def get(self, url):
            self.url = url
            return _Resp()

    monkeypatch.setattr(tg, "outbound_session", lambda **kw: _FakeSession())
    adapter = tg.TelegramAdapter("tok", "42", inbox_dir=str(tmp_path))
    assert await adapter._default_download("photos/x.jpg") == b"ok"
    assert len(made) == 1 and made[0].trust_env is True
    assert made[0].url.startswith("https://api.telegram.org/file/bot")


@pytest.mark.asyncio
async def test_the_llm_relay_call_matches_what_doctor_reports():
    """doctor 早就单独修过一次（_check_relay 用 trust_env=True），但只修一处更糟：
    **体检说"LLM 接口可达"，真正发请求的那条却直连失败**——报告和事实说的不是一回事。
    两边现在走同一个工厂。
    """
    import inspect

    from src.gateway import doctor
    from src.llm import client

    # 断的是"两处引用的是同一个函数对象"，不是源码里的字符串
    assert doctor.outbound_session is client.outbound_session is outbound_session
    assert inspect.iscoroutinefunction(doctor._check_relay)


@pytest.mark.asyncio
async def test_serve_health_check_stays_direct():
    """本地那一档不能跟着一起变——doctor 的 _check_serve 注释里记着为什么。"""
    from src.gateway import doctor

    assert doctor.local_session is local_session
