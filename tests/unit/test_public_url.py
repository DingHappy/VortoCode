"""PWA 深链（P4）的回归网。

一条纪律两面测：配了 → 该出现的两个时刻真的出现；没配 → **逐字节等于今天的行为**
（和卡片模板同款：新能力锁在配置门后，配错世界也不变坏）。
"""

from types import SimpleNamespace

import pytest

from src.gateway.public_url import agent_link, public_base_url
from src.im.bridge import IMBridge
from tests.unit.test_im_bridge import OWNER, FakeAdapter, ScriptedLLM


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VORTOCODE_PUBLIC_BASE_URL", raising=False)


# ---------------------------------------------------------------- 1. 解析
def test_unset_means_empty():
    assert public_base_url() == ""
    assert agent_link() == ""


def test_configured_yields_link(monkeypatch):
    monkeypatch.setenv("VORTOCODE_PUBLIC_BASE_URL", "http://192.168.10.97:8080/")
    assert public_base_url() == "http://192.168.10.97:8080"   # 尾斜杠收掉，别拼出 //agent
    assert agent_link() == "\n📱 http://192.168.10.97:8080/agent"


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "file:///etc/passwd",
                                 "192.168.10.97:8080", "ftp://x", "   "])
def test_non_http_schemes_are_refused(monkeypatch, bad):
    """钉钉消息里的链接会被点——javascript:/file: 从配置渗进消息就是埋雷。"""
    monkeypatch.setenv("VORTOCODE_PUBLIC_BASE_URL", bad)
    assert agent_link() == ""


# ---------------------------------------------------------------- 2. 接入点
def _bridge(tmp_path):
    return IMBridge(str(tmp_path), FakeAdapter(), OWNER, channel="test",
                    llm=ScriptedLLM("x"))


def _task(status="done", result="搞定了"):
    return SimpleNamespace(id="t-1", status=status, result=result, error="")


def test_final_notice_carries_link_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_PUBLIC_BASE_URL", "http://h:8080")
    text = _bridge(tmp_path)._task_final_text(_task())
    assert "后台任务 t-1 · done" in text and "搞定了" in text
    assert text.endswith("\n📱 http://h:8080/agent")


def test_final_notice_unchanged_when_unset(tmp_path):
    """没配 = 今天的行为，逐字节。"""
    assert _bridge(tmp_path)._task_final_text(_task()) == "后台任务 t-1 · done\n搞定了"


def test_failed_task_keeps_error_tail_and_link(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_PUBLIC_BASE_URL", "http://h:8080")
    text = _bridge(tmp_path)._task_final_text(_task(status="failed", result=""))
    assert "failed" in text and text.endswith("/agent")
