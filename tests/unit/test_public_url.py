"""公网深链的回归网。

浏览器对话台 /agent 已于 2026-10-17 退役：后台任务通知不再附 /agent 链接（点进去会是 404）；
流水线审批台 /review 仍在，审批通知照旧指路过去。没配公网地址时逐字节等于没有深链的行为。
"""

from types import SimpleNamespace

import pytest

from src.gateway.public_url import public_base_url, review_link
from src.im.bridge import IMBridge
from tests.unit.test_im_bridge import OWNER, FakeAdapter, ScriptedLLM


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VORTOCODE_PUBLIC_BASE_URL", raising=False)


# ---------------------------------------------------------------- 1. 解析
def test_unset_means_empty():
    assert public_base_url() == ""
    assert review_link("r-1") == ""


def test_configured_yields_review_link(monkeypatch):
    monkeypatch.setenv("VORTOCODE_PUBLIC_BASE_URL", "http://192.168.10.97:8080/")
    assert public_base_url() == "http://192.168.10.97:8080"   # 尾斜杠收掉，别拼出 //review
    assert review_link("r-1") == "\n📱 http://192.168.10.97:8080/review（r-1）"


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "file:///etc/passwd",
                                 "192.168.10.97:8080", "ftp://x", "   "])
def test_non_http_schemes_are_refused(monkeypatch, bad):
    """钉钉消息里的链接会被点——javascript:/file: 从配置渗进消息就是埋雷。"""
    monkeypatch.setenv("VORTOCODE_PUBLIC_BASE_URL", bad)
    assert review_link() == ""


# ---------------------------------------------------------------- 2. 接入点
def _bridge(tmp_path):
    return IMBridge(str(tmp_path), FakeAdapter(), OWNER, channel="test",
                    llm=ScriptedLLM("x"))


def _task(status="done", result="搞定了"):
    return SimpleNamespace(id="t-1", status=status, result=result, error="")


@pytest.mark.parametrize("configured", [False, True])
def test_final_notice_never_links_the_retired_console(tmp_path, monkeypatch, configured):
    if configured:
        monkeypatch.setenv("VORTOCODE_PUBLIC_BASE_URL", "http://h:8080")
    assert _bridge(tmp_path)._task_final_text(_task()) == "后台任务 t-1 · done\n搞定了"


def test_failed_task_keeps_error_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_PUBLIC_BASE_URL", "http://h:8080")
    text = _bridge(tmp_path)._task_final_text(_task(status="failed", result=""))
    assert text.startswith("后台任务 t-1 · failed") and "/agent" not in text
