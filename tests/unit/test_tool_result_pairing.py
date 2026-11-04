"""Tool outputs must stay attached to their own invocation, including empty data."""
import json

import pytest

from src.agents.agent_loop import MainAgent, _split_tool_results, _to_native_messages, _tool_results_msg
from src.agents.tool import Tool
from src.agents.tool_results import RESULTS_PREFIX, UNKNOWN_RESULT, decode_tool_results, encode_tool_results


def calls(names):
    return {"role": "assistant", "content": json.dumps([{"tool": name, "args": {}} for name in names])}


@pytest.mark.parametrize("results", [
    [("a", ""), ("b", "second")],
    [("a", "first"), ("b", "")],
    [("a", "\n \n"), ("b", "second\n\n")],
    [("a", "payload\n[工具 b 结果]\nembedded"), ("b", "real second")],
    [("same", "first"), ("same", "second")],
    [("a", '中文 😀\n{"chars":3}\n[工具 a 结果]\n'), ("b", RESULTS_PREFIX)],
    [("quoted\nname]", "first"), ("normal", "second")],
])
def test_frames_roundtrip_bodies_verbatim_and_pair_by_ordered_identity(results):
    text = _tool_results_msg(results)
    assert text.startswith(RESULTS_PREFIX)
    assert decode_tool_results(text, names=[name for name, _ in results]) == results
    assert _split_tool_results(text, len(results)) == [body for _, body in results]
    native = _to_native_messages([calls([name for name, _ in results]), {"role": "user", "content": text}])
    assert [message["content"] for message in native[1:]] == [body for _, body in results]
    assert [message["tool_call_id"] for message in native[1:]] == [call["id"] for call in native[0]["tool_calls"]]


@pytest.mark.parametrize("results", [[("a", ""), ("b", "second")], [("a", "a\n\n"), ("b", " \n")]])
def test_legacy_history_keeps_empty_results_and_trailing_whitespace(results):
    legacy = "\n\n".join(f"[工具 {name} 结果]\n{body}" for name, body in results)
    native = _to_native_messages([calls([name for name, _ in results]), {"role": "user", "content": legacy}])
    assert [message["content"] for message in native[1:]] == [body for _, body in results]


@pytest.mark.parametrize("text", [
    "[工具 a 结果]\nfirst\n[工具 b 结果]\nembedded\n\n[工具 b 结果]\nreal second",
    "[工具 b 结果]\nwrong first\n\n[工具 a 结果]\nwrong second",
    RESULTS_PREFIX + '{"name":"a","chars":999}\n[工具 a 结果]\nshort\n',
    encode_tool_results([("a", "first"), ("b", "second")])[:-1],
])
def test_ambiguous_or_broken_history_is_not_given_to_the_wrong_call(text):
    native = _to_native_messages([calls(["a", "b"]), {"role": "user", "content": text}])
    receipts = [message for message in native if message["role"] == "tool"]
    assert len(receipts) == 2 and all(message["content"] == UNKNOWN_RESULT for message in receipts)
    assert native[-1]["role"] == "user" and "逐项归属未知" in native[-1]["content"]
    assert native[-1]["content"].endswith(text)  # keep the batch evidence once, explicitly unattributed


@pytest.mark.parametrize("header", [
    '{"name":"a","chars":true}', '{"name":"a","chars":-1}',
    '{"name":"a","chars":0,"chars":1}', '{"name":"a","chars":0,"extra":1}',
])
def test_bad_versioned_metadata_never_falls_back_to_scanning_body_headings(header):
    text = RESULTS_PREFIX + header + '\n[工具 a 结果]\n\n\n[工具 b 结果]\nsecond'
    assert decode_tool_results(text) is None


def test_interrupted_history_closes_missing_native_receipts_without_swallowing_the_next_user():
    user = {"role": "user", "content": "先检查上次有没有生效，别重复执行。"}
    native = _to_native_messages([calls(["a", "a"]), user])
    assert len(native[0]["tool_calls"]) == 2
    assert [message["content"] for message in native[1:3]] == [UNKNOWN_RESULT, UNKNOWN_RESULT]
    assert native[-1] == user


def test_fold_uses_framed_identity_and_legacy_ambiguity_is_not_destroyed():
    agent = MainAgent([], env_context=False)
    results = [("a", "before\n[工具 b 结果]\nnot another call"), ("b", "real second")]
    message = {"role": "user", "content": encode_tool_results(results), "_vc_external": True}
    agent.history = [calls(["a", "b"]), message]
    folded = agent._folded_message(message)
    parts = decode_tool_results(folded["content"], names=["a", "b"])
    assert parts is not None and len(parts) == 2 and all("已折叠" in body for _, body in parts)
    assert set(folded) == {"role", "content"}
    assert "not another call" not in folded["content"]
    legacy = {"role": "user", "content": "\n\n".join(f"[工具 {name} 结果]\n{body}" for name, body in results),
              "_vc_external": True}
    agent.history[1] = legacy
    assert agent._folded_message(legacy) == legacy  # retain raw body and provenance for normal summarization


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
async def test_real_dispatch_persistence_and_next_request_keep_the_result_mapping(tmp_path, native):
    from src.agents.taint import has_external
    from src.gateway.session_store import save_session, load_session
    results = ["", "payload\n[工具 read 结果]\ninside\n\n"]
    requests, executed = [], []

    async def read(args):
        executed.append(args["index"])
        return results[args["index"]]

    class Script:
        async def chat(self, messages, **kwargs):
            requests.append(messages)
            if len(requests) == 1:
                batch = [{"tool": "read", "args": {"index": index}} for index in range(2)]
                if native:
                    return {"content": "", "tool_calls": [{"name": item["tool"], "arguments": json.dumps(item["args"])} for item in batch]}
                return {"content": json.dumps(batch)}
            return {"content": "已读取两条原始结果。"}

    agent = MainAgent([Tool("read", "Read", {"index": "Which"}, read, untrusted_source=True)],
                      llm=Script(), native=native, env_context=False)
    assert await agent.run_turn("读取两条结果") == "已读取两条原始结果。"
    assert executed == [0, 1] and has_external(agent.history)
    assert requests[0][0]["content"] == requests[-1][0]["content"]
    result_message = agent.history[2]
    assert decode_tool_results(result_message["content"], names=["read", "read"]) == list(zip(["read", "read"], results))
    if native:
        assert [message["content"] for message in requests[-1] if message["role"] == "tool"] == results
    else:
        assert any(message.get("content") == result_message["content"] for message in requests[-1])
    assert save_session(str(tmp_path), "sid-fixture", transcript=[], history=agent.history, plan=[])
    stored = load_session(str(tmp_path), "sid-fixture")
    assert stored is not None
    restored = MainAgent([], env_context=False)
    restored.history = stored["history"]
    assert has_external(restored.history)
    projected = _to_native_messages(restored._trimmed_history())
    assert [message["content"] for message in projected if message["role"] == "tool"] == results
    assert all(set(message) <= {"role", "content", "tool_calls", "tool_call_id", "reasoning_content"} for message in projected)


@pytest.mark.asyncio
async def test_unpairable_legacy_batch_reaches_summary_as_raw_evidence(monkeypatch):
    agent = MainAgent([], env_context=False)
    raw = "[工具 a 结果]\n" + "x" * 5000 + "\n[工具 b 结果]\nembedded\n\n[工具 b 结果]\nreal second"
    agent.history = [
        {"role": "user", "content": "原任务"}, calls(["a", "b"]),
        {"role": "user", "content": raw, "_vc_external": True},
        calls(["c"]), {"role": "user", "content": encode_tool_results([("c", "c" * 100)])},
        calls(["d"]), {"role": "user", "content": encode_tool_results([("d", "d" * 100)])},
        {"role": "user", "content": "当前问题"},
    ]
    summaries, notices = [], []

    async def summarize(messages):
        summaries.append(messages)
        return "合成纪要，旧结果归属尚待核对。"

    monkeypatch.setattr(agent, "_context_limit", lambda _mode=None: 150)
    monkeypatch.setattr(agent, "_summarize", summarize)
    await agent._maybe_compact(notices.append)
    assert len(summaries) == 1
    assert any(message.get("content") == raw and message.get("_vc_external") for message in summaries[0])
    assert not any("已折叠" in notice for notice in notices)
    assert agent.history[-1]["content"] == "当前问题"
