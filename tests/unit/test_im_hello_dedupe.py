"""开机横幅去重——别让"部署"变成刷屏。

背景（2026-08-01 真机）：横幅挂在**每次进程启动**上，而部署就是重启。一晚上部署八次，
主人的对话里就插了八条一模一样的「已就绪」，把真正的卡片和结果冲散了。

横幅本身有用（久宕后恢复、白名单配错），坏在 routine 重启也照说不误。这里钉住三件事：
①连着重启只说第一次 ②隔久了照样报平安 ③**有警告时永远说**（漏一次能让人排查一整晚）。
"""

import json
import time

import pytest

from src.im.bridge import IMBridge
from tests.unit.test_im_bridge import OWNER, FakeAdapter, ScriptedLLM


def _bridge(tmp_path, **kw):
    return IMBridge(str(tmp_path), FakeAdapter(), OWNER, channel="test",
                    llm=ScriptedLLM("x"), **kw)


@pytest.fixture(autouse=True)
def _default_window(monkeypatch):
    monkeypatch.delenv("VORTOCODE_IM_HELLO_QUIET_MIN", raising=False)


def test_first_start_always_speaks(tmp_path):
    """没有任何记录 → 说。第一次装好就该有个回音。"""
    assert _bridge(tmp_path)._should_say_hello() is True


def test_second_start_within_window_stays_quiet(tmp_path):
    """连着重启（部署）只说第一次——这正是要解决的那条。"""
    b = _bridge(tmp_path)
    b._mark_hello_said()
    assert b._should_say_hello() is False


def test_speaks_again_after_a_long_gap(tmp_path):
    """真宕了很久再回来，照样报平安——去重不能把"恢复"这个信号也吃掉。"""
    b = _bridge(tmp_path)
    b._mark_hello_said()
    p = b._hello_stamp_path()
    p.write_text(json.dumps({"at": time.time() - 3600}), encoding="utf-8")   # 一小时前
    assert b._should_say_hello() is True


@pytest.mark.parametrize("junk", ["", "不是 json", "{}", '{"at": "昨天"}', '{"at": null}'])
def test_broken_stamp_falls_back_to_speaking(tmp_path, junk):
    """记录坏了/缺字段 → 当作没说过。**宁可多说一句，不可把恢复信号弄丢。**"""
    b = _bridge(tmp_path)
    p = b._hello_stamp_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(junk, encoding="utf-8")
    assert b._should_say_hello() is True


def test_zero_window_restores_old_behavior(tmp_path, monkeypatch):
    """设 0 = 每次都说，等于加这个功能之前的行为——留给想要它的人。"""
    monkeypatch.setenv("VORTOCODE_IM_HELLO_QUIET_MIN", "0")
    b = _bridge(tmp_path)
    b._mark_hello_said()
    assert b._should_say_hello() is True


@pytest.mark.parametrize("bogus", ["很久", "-5", "abc"])
def test_bogus_window_does_not_crash_startup(tmp_path, monkeypatch, bogus):
    """窗口值写错不该让桥起不来——回落默认，且判定仍然可用。"""
    monkeypatch.setenv("VORTOCODE_IM_HELLO_QUIET_MIN", bogus)
    b = _bridge(tmp_path)
    assert isinstance(b._should_say_hello(), bool)


@pytest.mark.asyncio
async def test_warning_is_never_suppressed(tmp_path, monkeypatch):
    """**白名单配错的提醒任何时候都要发**，哪怕刚说过。

    那种配错的表现是"机器人装死"，提醒漏一次可能让人排查一整晚——
    去重是为了省噪音，不能把唯一的诊断线索一起省掉。
    """
    monkeypatch.setenv("VORTOCODE_IM_ALLOW_FROM", "someone-else")
    adapter = FakeAdapter()
    b = IMBridge(str(tmp_path), adapter, OWNER, channel="test", llm=ScriptedLLM("x"),
                 allow_from={"someone-else"})
    b._mark_hello_said()                      # 刚说过 → routine 横幅本该被压掉
    assert b._should_say_hello() is False

    adapter.stop()
    await b.run()
    sent = "\n".join(adapter.texts())
    assert "白名单" in sent, "白名单警告被去重逻辑吞掉了——那是最难自查的一类配错"


@pytest.mark.asyncio
async def test_quiet_restart_sends_nothing(tmp_path):
    """窗口内重启：一条都不该发。"""
    adapter = FakeAdapter()
    b = IMBridge(str(tmp_path), adapter, OWNER, channel="test", llm=ScriptedLLM("x"))
    b._mark_hello_said()
    adapter.stop()
    await b.run()
    assert not [t for t in adapter.texts() if "已就绪" in t]
