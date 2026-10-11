"""回合工作记录摘要：模型生成优先，任何失败都退回原文截取，绝不影响对话。"""
from __future__ import annotations

import asyncio

import pytest

from src.gateway.turn_digest import (
    fallback_digest,
    generate_digest,
    parse_digest,
    recent_turns,
    turn_tools,
)


class _LLM:
    def __init__(self, content=None, error=None, delay=0.0):
        self.content, self.error, self.delay, self.calls = content, error, delay, []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return {"content": self.content}


def test_parse_digest_takes_first_json_object_and_clips():
    assert parse_digest('好的：{"title": "调研 VortoCode 项目", "outcome": "完成静态审查并归档记录"} 以上') == {
        "title": "调研 VortoCode 项目", "outcome": "完成静态审查并归档记录", "source": "model"}
    long = parse_digest('{"title": "' + "很" * 50 + '", "outcome": "' + "长" * 80 + '"}')
    assert len(long["title"]) == 20 and long["title"].endswith("…") and len(long["outcome"]) == 40
    for bad in ("不是 JSON", '{"title": ""}', '{"title": 3, "outcome": "x"}', "{broken"):
        assert parse_digest(bad) is None


def test_fallback_keeps_identifiers_and_first_sentence():
    digest = fallback_digest("## 修 `timeout_scope`。顺便看看测试", "已补 **add_note** 垫片。细节见下")
    assert digest == {"title": "修 timeout_scope。", "outcome": "已补 add_note 垫片。", "source": "text"}
    assert fallback_digest("", "")["title"] == "未命名事项"


async def test_generate_digest_uses_model_output():
    llm = _LLM('{"title": "查询宁波天气", "outcome": "给出今明两天天气与出行建议"}')
    digest = await generate_digest(llm, "宁波", "今天宁波晴，25°C……", ["搜索网页：宁波 天气"])
    assert digest == {"title": "查询宁波天气", "outcome": "给出今明两天天气与出行建议", "source": "model"}
    messages, kwargs = llm.calls[0]
    assert "搜索网页：宁波 天气" in messages[1]["content"] and kwargs["max_tokens"] == 160


@pytest.mark.parametrize("llm", [
    None,
    _LLM(error=RuntimeError("relay down")),
    _LLM(content="抱歉，我无法总结"),
    _LLM(content='{"title": "慢"}', delay=1.0),
])
async def test_generate_digest_falls_back_to_text_on_any_failure(llm):
    digest = await generate_digest(llm, "检查本地模型。还有别的", "连接正常。", timeout=0.05)
    assert digest == {"title": "检查本地模型。", "outcome": "连接正常。", "source": "text"}


def test_turn_tools_counts_each_call_once():
    events = [
        {"rid": "r1", "type": "agent_tool", "id": "a", "summary": "搜索网页", "status": "running"},
        {"rid": "r1", "type": "agent_tool", "id": "a", "summary": "搜索网页", "status": "succeeded"},
        {"rid": "r1", "type": "agent_tool", "id": "b", "name": "read_file"},
        {"rid": "r1", "type": "agent_phase", "id": "p"},
        {"rid": "r2", "type": "agent_tool", "id": "c", "summary": "别的回合"},
    ]
    assert turn_tools(events, "r1") == ["搜索网页", "read_file"]


def test_recent_turns_across_sessions_newest_first(tmp_path):
    from src.gateway.session_store import save_session

    save_session(str(tmp_path), "sid-a", [
        {"role": "user", "text": "调研项目", "rid": "r1", "at": "2026-10-11T01:00:00+00:00"},
        {"role": "assistant", "text": "完成了。", "rid": "r1", "at": "2026-10-11T01:05:00+00:00",
         "digest": {"title": "调研项目", "outcome": "完成静态审查", "source": "model"}},
        {"role": "user", "text": "再查一下依赖", "rid": "r2", "at": "2026-10-11T03:00:00+00:00"},
    ], [{"role": "user", "content": "x"}], None, title="项目调研",
        activities=[{"rid": "r1", "type": "agent_tool", "id": "t1", "summary": "读取文件"}])
    save_session(str(tmp_path), "sid-b", [
        {"role": "user", "text": "宁波天气", "rid": "r9", "at": "2026-10-11T02:00:00+00:00"},
        {"role": "assistant", "text": "晴，25°C。", "rid": "r9", "at": "2026-10-11T02:00:30+00:00"},
    ], [{"role": "user", "content": "x"}], None, title="天气")

    turns = recent_turns(str(tmp_path))
    assert [turn["rid"] for turn in turns] == ["r2", "r9", "r1"]
    first, weather, research = turns
    assert first["status"] == "no_reply" and first["title"] == "再查一下依赖"
    assert weather == {**weather, "title": "宁波天气", "outcome": "晴，25°C。", "digest_source": "text", "session_title": "天气"}
    assert research == {**research, "title": "调研项目", "outcome": "完成静态审查", "digest_source": "model", "tools": 1}
    assert len(recent_turns(str(tmp_path), limit=1)) == 1


async def test_turn_end_writes_digest_into_transcript_and_persists(tmp_path, monkeypatch):
    """真实的回合收尾路径：_digest_turn 把模型摘要写进该轮最终回复并存盘。"""
    from src.gateway.session_store import load_session
    from src.web.routers import realtime

    llm = _LLM('{"title": "查询宁波天气", "outcome": "给出今明两天天气"}')

    class _Agent:
        def _client(self):
            return llm

    key = "sid-digest"
    realtime._SESSIONS[key] = {
        "agent": _Agent(), "repo_root": str(tmp_path), "history": [], "plan": [], "activities": [],
        "transcript": [{"role": "user", "text": "宁波", "rid": "r1"}, {"role": "assistant", "text": "晴。", "rid": "r1"}],
    }
    try:
        await realtime._digest_turn(key, "r1")
        reply = realtime._SESSIONS[key]["transcript"][1]
        assert reply["digest"] == {"title": "查询宁波天气", "outcome": "给出今明两天天气", "source": "model"}
        assert load_session(str(tmp_path), key)["transcript"][1]["digest"]["title"] == "查询宁波天气"
        await realtime._digest_turn(key, "r1")          # 已有摘要不重复生成
        assert len(llm.calls) == 1
    finally:
        realtime._SESSIONS.pop(key, None)


def test_old_archives_without_at_take_time_from_turn_events(tmp_path):
    from src.gateway.session_store import save_session

    save_session(str(tmp_path), "sid-old", [{"role": "user", "text": "旧回合", "rid": "r1"},
                                            {"role": "assistant", "text": "好。", "rid": "r1"}],
                 [{"role": "user", "content": "x"}], None,
                 activities=[{"rid": "r1", "type": "agent_phase", "recorded_at": "2026-10-09T01:00:00+00:00"},
                             {"rid": "r1", "type": "agent_phase", "recorded_at": "2026-10-09T01:00:30+00:00"}])
    assert recent_turns(str(tmp_path))[0]["at"] == "2026-10-09T01:00:30+00:00"

