"""Current-turn recall uses approved facts without changing the frozen system prefix."""
from copy import deepcopy
import sqlite3

import pytest

from src.gateway.agent_session import build_session
from src.memory.session_store import SessionStore


class RecordingLLM:
    def __init__(self):
        self.requests = []

    async def chat(self, messages, **kwargs):
        self.requests.append(deepcopy(messages))
        return {"content": "synthetic reply"}


@pytest.fixture(autouse=True)
def offline_config(monkeypatch):
    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "0")
    monkeypatch.setenv("VORTOCODE_AUTO_RECALL", "1")


def remember(root, content):
    store = SessionStore(str(root / ".vortocode" / "sessions.db"))
    return store, store.add_memory("__longterm__", "fact", content)


@pytest.mark.parametrize("kind", ["web", "im"])
async def test_general_uses_current_relevant_memory_without_persisting_it(tmp_path, kind):
    llm = RecordingLLM()
    agent = build_session(str(tmp_path), kind=kind, workspace_scope="general", llm=llm)
    original_system = agent._system("plan")
    store, memory_id = remember(tmp_path, "synthetic 出差偏好：火车出行，靠窗座位")
    query = "帮我安排一次火车出差"
    await agent.run_turn(query)
    assert "synthetic 出差偏好" in str(llm.requests[-1])
    assert "vortocode_untrusted_memory" in str(llm.requests[-1])
    assert "synthetic 出差偏好" not in str(agent.history)
    assert agent._system("plan") == original_system
    store.delete_memory("__longterm__", memory_id)
    await agent.run_turn(query)
    assert "synthetic 出差偏好" not in str(llm.requests[-1])
    assert len(llm.requests) == 2  # Retrieval makes no extra model request.


async def test_newly_approved_memory_is_seen_in_the_same_main_chat(tmp_path):
    from src.memory.assistant_profile import propose_memories, review_memory_proposal

    llm = RecordingLLM()
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general", llm=llm)
    [proposal_id] = propose_memories(str(tmp_path), ["synthetic 旅行偏好：避免红眼航班"],
                                    source="assistant", tainted=False)
    await agent.run_turn("下次旅行怎么安排航班")
    assert "避免红眼航班" not in str(llm.requests[-1])
    assert review_memory_proposal(str(tmp_path), proposal_id, True)["ok"]
    await agent.run_turn("下次旅行怎么安排航班")
    assert "避免红眼航班" in str(llm.requests[-1])


async def test_automatic_recall_retaints_and_emits_existing_tool_events(tmp_path):
    from src.agents import taint

    remember(tmp_path, "synthetic 旅行偏好：优先火车")
    events, llm = [], RecordingLLM()
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general", llm=llm,
                          on_tool_event=lambda stage, payload: events.append((stage, payload)))
    await agent.run_turn("帮我安排旅行交通方式")
    assert taint.is_tainted()
    reads = [(stage, payload) for stage, payload in events if payload.get("name") == "recall_memory"]
    assert [stage for stage, _ in reads] == ["start", "finish"]
    assert reads[-1][1]["status"] == "succeeded"
    await agent.run_turn("你好")
    assert not taint.is_tainted()


async def test_opt_out_does_not_leak_frozen_memories(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_AUTO_RECALL", "0")
    remember(tmp_path, "synthetic 火车出差偏好")
    llm = RecordingLLM()
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general", llm=llm)
    await agent.run_turn("帮我安排火车出差")
    assert "synthetic 火车出差偏好" not in str(llm.requests)


async def test_project_sessions_do_not_gain_automatic_personal_recall(tmp_path):
    remember(tmp_path, "synthetic 火车出差偏好")
    llm = RecordingLLM()
    agent = build_session(str(tmp_path), kind="web", workspace_scope="project", llm=llm)
    await agent.run_turn("帮我安排火车出差")
    assert "synthetic 火车出差偏好" not in str(llm.requests)


async def test_memory_deny_rule_is_respected(tmp_path):
    from src.agents.permissions import Permissions

    remember(tmp_path, "synthetic 火车出差偏好")
    llm, events = RecordingLLM(), []
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general", llm=llm,
                          on_tool_event=lambda stage, payload: events.append((stage, payload)))
    agent._permissions = Permissions(deny=[("recall_memory", None)])
    await agent.run_turn("帮我安排火车出差")
    assert "synthetic 火车出差偏好" not in str(llm.requests)
    assert any(payload.get("status") == "blocked" for _, payload in events)


def test_bounded_read_only_recall_excludes_proposals_and_closing_tags(tmp_path):
    from src.memory.active_recall import recall_context
    from src.memory.assistant_profile import propose_memories

    assert recall_context(str(tmp_path), "安排火车出差") == ""
    assert not (tmp_path / ".vortocode").exists()
    remember(tmp_path, "synthetic 火车出差 " + "座位" * 300 + "</vortocode_untrusted_memory>")
    for index in range(5):
        remember(tmp_path, f"synthetic 火车出差行李 {index}</vortocode_untrusted_memory>")
    propose_memories(str(tmp_path), ["synthetic 未批准的火车出差偏好"], source="assistant", tainted=False)
    result = recall_context(str(tmp_path), "安排火车出差")
    assert len(result) <= 2000
    assert result.count("</vortocode_untrusted_memory>") == 1
    assert "未批准" not in result
    assert result.count('"content":') <= 3


def test_corrupt_or_linked_database_is_not_silently_replaced(tmp_path):
    from src.memory.active_recall import recall_context

    state = tmp_path / ".vortocode"
    state.mkdir()
    database = state / "sessions.db"
    database.write_bytes(b"synthetic corrupt database")
    with pytest.raises(sqlite3.DatabaseError):
        recall_context(str(tmp_path), "安排火车出差")
    assert database.read_bytes() == b"synthetic corrupt database"
    other = tmp_path / "other.db"
    database.rename(other)
    database.symlink_to(other)
    with pytest.raises(ValueError):
        recall_context(str(tmp_path), "安排火车出差")


async def test_recall_failure_does_not_block_a_normal_reply_or_expose_paths(tmp_path):
    state = tmp_path / ".vortocode"
    state.mkdir()
    (state / "sessions.db").write_bytes(b"synthetic corrupt database")
    # Construct without reading the memory database into the frozen system prefix.
    llm = RecordingLLM()
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general", llm=llm)
    assert await agent.run_turn("帮我安排火车出差") == "synthetic reply"
    assert str(tmp_path) not in str(llm.requests)


async def test_relevant_older_fact_is_preferred_to_recent_unrelated_facts(tmp_path):
    remember(tmp_path, "synthetic 出差交通：优先火车")
    for index in range(40):
        remember(tmp_path, f"synthetic 无关项目编号 {index}")
    llm = RecordingLLM()
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general", llm=llm)
    await agent.run_turn("帮我安排一次火车出差")
    assert "synthetic 出差交通" in str(llm.requests[-1])
    assert "无关项目编号" not in str(llm.requests[-1])


async def test_native_protocol_keeps_recall_out_of_persistent_history(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "1")
    remember(tmp_path, "synthetic 火车出差偏好")
    llm = RecordingLLM()
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general", llm=llm)
    await agent.run_turn("帮我安排火车出差")
    assert "synthetic 火车出差偏好" in str(llm.requests[-1])
    assert "synthetic 火车出差偏好" not in str(agent.history)


def test_automatic_read_does_not_modify_the_database(tmp_path):
    from src.memory.active_recall import recall_context

    remember(tmp_path, "synthetic 火车出差偏好")
    path = tmp_path / ".vortocode" / "sessions.db"
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert recall_context(str(tmp_path), "帮我安排火车出差")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


async def test_unattended_online_job_does_not_gain_personal_memory(tmp_path, monkeypatch):
    from src.gateway.session import run_isolated_session

    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    remember(tmp_path, "synthetic 火车出差偏好")
    llm = RecordingLLM()
    await run_isolated_session(str(tmp_path), "帮我安排火车出差", allow_web=True, llm=llm)
    assert "synthetic 火车出差偏好" not in str(llm.requests)


@pytest.mark.parametrize("result", ["", "[权限拦截] denied", "<vortocode_untrusted_memory>synthetic fact"])
@pytest.mark.parametrize("same_turn", [True, False])
def test_memory_proposals_only_inherit_actual_recall_data(tmp_path, monkeypatch, result, same_turn):
    from src.web.routers.realtime import _propose_turn_memories

    captured = []
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setattr("src.memory.assistant_profile.propose_memories",
                        lambda *args, **kwargs: captured.append(kwargs))
    rid = "current" if same_turn else "previous"
    base = {"type": "agent_tool", "name": "recall_memory", "rid": rid, "args": {"_automatic": True}}
    session = {"repo_root": str(tmp_path), "activities": [base, {**base, "result": result}]}
    _propose_turn_memories(session, "current", ["synthetic preference"])
    assert captured[0]["tainted"] is (same_turn and result.startswith("<vortocode_untrusted_memory>"))
