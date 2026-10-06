"""Real delegation wiring and durable review/feedback lifecycle, without network."""
import asyncio
import ast
import json
import re
from pathlib import Path

import pytest

from src.agents.main_agent import MainAgent, build_agent_tools, build_research_tools
from src.agents import taint
from src.gateway.collaboration import CollaborationConflict, CollaborationService
from src.gateway.tasks import TaskLedger
from src.gateway.decisions import build_decision_queue
from src.gateway.worktree_sessions import task_session_view


class EchoLLM:
    async def chat(self, messages, **kwargs):
        return {"content": "子任务交付结果"}


def tools(repo, owner="sid-parent", **kwargs):
    service = CollaborationService(str(repo), owner)
    built = build_research_tools(str(repo), llm=EchoLLM(), collaboration=service, **kwargs)
    return service, {tool.name: tool for tool in built}


@pytest.mark.asyncio
async def test_parent_receives_submission_and_reviews_in_same_turn(tmp_path):
    service, by = tools(tmp_path)

    class ParentLLM:
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"content": json.dumps({"tool": "task", "args": {
                    "description": "分析认证模块", "acceptance": ["说明认证入口"],
                }})}
            if self.calls == 2:
                # The child result is actually fed back to the parent model.
                content = str(messages)
                assert "子任务交付结果" in content and "pending" in content
                tid = re.search(r"task-[a-f0-9]{10}", content).group()
                assert service.get(tid).status == "done"
                assert service.get(tid).collaboration["review"] == "pending"
                return {"content": json.dumps({"tool": "task_review", "args": {
                    "task_id": tid, "round": 1, "verdict": "accept", "note": "已检查认证入口分析",
                }})}
            return {"content": "已审查子任务并汇总交付"}

    parent = MainAgent(list(by.values()), llm=ParentLLM())
    assert "汇总交付" in await parent.run_turn("研究认证入口", mode="plan")
    task = service.list()[0]
    assert task.collaboration["review"] == "accepted"
    assert [m["kind"] for m in task.collaboration["messages"]] == ["assigned", "started", "submitted", "accept"]


@pytest.mark.asyncio
async def test_feedback_rebuilds_child_from_persisted_history(tmp_path):
    service, by = tools(tmp_path)
    first = json.loads(await by["task"].handler({"description": "分析入口"}))
    await by["task_review"].handler({"task_id": first["task_id"], "round": 1,
                                     "verdict": "rework", "note": "缺少错误处理说明"})
    prompts = []

    class CaptureLLM:
        async def chat(self, messages, **kwargs):
            prompts.append(str(messages))
            return {"content": "已补充错误处理"}

    # A new factory/service has no in-memory child history.
    restored = CollaborationService(str(tmp_path), "sid-parent")
    by = {t.name: t for t in build_research_tools(
        str(tmp_path), llm=CaptureLLM(), collaboration=restored)}
    second = json.loads(await by["task_followup"].handler({
        "task_id": first["task_id"], "round": 1, "message": "补充认证失败时的处理",
    }))
    assert second["task_id"] == first["task_id"] and second["round"] == 2
    assert second["review"] == "pending"
    assert all(word in prompts[0] for word in ["分析入口", "子任务交付结果", "缺少错误处理说明", "认证失败"])
    with pytest.raises(CollaborationConflict):
        service.review(first["task_id"], 1, "accept", "过期审查")
    with pytest.raises(CollaborationConflict):
        service.finish(first["task_id"], 1, "过期执行结果")


def test_owner_round_and_transition_boundaries(tmp_path):
    service = CollaborationService(str(tmp_path), "sid-a")
    task = service.create("研究")
    with pytest.raises(CollaborationConflict):
        CollaborationService(str(tmp_path), "sid-b").get(task.id)
    assert CollaborationService(str(tmp_path), "sid-b").list() == []
    with pytest.raises(CollaborationConflict):
        service.get("../" + task.id)
    with pytest.raises(CollaborationConflict):
        service.review(task.id, None, "accept", "缺少轮次")
    with pytest.raises(CollaborationConflict):
        service.review(task.id, 1, "accept", "尚未执行")
    service.start(task.id, 1)
    with pytest.raises(CollaborationConflict):
        service.start(task.id, 1)
    service.finish(task.id, 1, "结论")
    with pytest.raises(CollaborationConflict):
        service.finish(task.id, 1, "重复结果")
    service.review(task.id, 1, "accept", "已检查")
    count = len(service.get(task.id).collaboration["messages"])
    service.review(task.id, 1, "accept", "重复投递")
    assert len(service.get(task.id).collaboration["messages"]) == count
    with pytest.raises(CollaborationConflict):
        service.followup(task.id, 1, "已验收后偷偷追加工作")


def test_recovery_and_hard_round_limit(tmp_path):
    service = CollaborationService(str(tmp_path), "sid-a")
    task = service.create("研究")
    service.start(task.id, 1)
    TaskLedger(str(tmp_path)).recover_interrupted()
    assert service.get(task.id).status == "interrupted"
    service.followup(task.id, 1, "恢复研究")
    service.start(task.id, 2)
    service.finish(task.id, 2, "", error="模型服务不可用")
    assert service.get(task.id).status == "failed"
    service.followup(task.id, 2, "重试")
    service.start(task.id, 3)
    service.finish(task.id, 3, "结果")
    with pytest.raises(CollaborationConflict, match="上限"):
        service.followup(task.id, 3, "无限重试")


@pytest.mark.asyncio
async def test_storage_failure_prevents_child_execution(tmp_path, monkeypatch):
    service, by = tools(tmp_path)
    monkeypatch.setattr(service.ledger, "save", lambda task: False)
    calls = []

    async def child(*args, **kwargs):
        calls.append(True)
        return "不应执行"

    monkeypatch.setattr(MainAgent, "run_turn", child)
    with pytest.raises(OSError):
        await by["task"].handler({"description": "研究"})
    assert not calls


@pytest.mark.asyncio
async def test_model_failure_and_cancellation_are_not_successful_submissions(tmp_path):
    class BrokenLLM:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("model unavailable")

    service = CollaborationService(str(tmp_path), "sid-parent")
    by = {t.name: t for t in build_research_tools(str(tmp_path), llm=BrokenLLM(), collaboration=service)}
    result = json.loads(await by["task"].handler({"description": "研究"}))
    assert result["status"] == "failed" and result["review"] == "not_submitted"

    entered = asyncio.Event()

    class BlockingLLM:
        async def chat(self, messages, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    by = {t.name: t for t in build_research_tools(str(tmp_path), llm=BlockingLLM(), collaboration=service)}
    running = asyncio.create_task(by["task"].handler({"description": "取消研究"}))
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert service.list()[0].status == "cancelled"


@pytest.mark.asyncio
async def test_persisted_taint_and_secrets_are_not_lost_on_handoff(tmp_path, monkeypatch):
    service, by = tools(tmp_path)

    async def child(self, *args, **kwargs):
        taint.reset_taint()
        taint.mark_tainted()
        return "API_KEY=private-value\nexternal result"

    monkeypatch.setattr(MainAgent, "run_turn", child)
    try:
        result = json.loads(await by["task"].handler({"description": "研究"}))
        assert taint.is_tainted()
        assert "private-value" not in service.get(result["task_id"]).result
        taint.reset_taint()
        restored, by = tools(tmp_path)
        await by["task_status"].handler({"task_id": result["task_id"]})
        assert taint.is_tainted()
        assert restored.get(result["task_id"]).collaboration["tainted"]
    finally:
        taint.reset_taint()


@pytest.mark.asyncio
async def test_dev_followup_keeps_confirmation_and_role_boundary(tmp_path):
    roles = tmp_path / ".vortocode" / "agents"
    roles.mkdir(parents=True)
    (roles / "coder.md").write_text("---\ntools: dev\n---\n你是实现工程师。", encoding="utf-8")
    decisions = iter([True, False])
    asked = []

    async def confirm(message):
        asked.append(message)
        return next(decisions)

    service, by = tools(tmp_path, confirm=confirm)
    first = json.loads(await by["task"].handler({"description": "实现小功能", "agent": "coder"}))
    second = json.loads(await by["task_followup"].handler({
        "task_id": first["task_id"], "round": 1, "message": "修复失败路径", "agent": "other",
    }))
    assert len(asked) == 2
    assert second["assignee"] == "coder" and second["status"] == "cancelled"
    assert service.get(first["task_id"]).collaboration["review"] == "not_submitted"


def test_standard_factory_wires_collaboration_and_modules_do_not_import_facade(tmp_path):
    by = {t.name: t for t in build_agent_tools(str(tmp_path), confirm=None, task_owner="sid-a",
                                            with_web=False, with_cron=False, with_dev=False)}
    assert {"task", "task_status", "task_review", "task_followup"} <= set(by)
    root = Path(__file__).resolve().parents[2]
    for file in ["src/agents/dev_tools.py", "src/agents/dev_policy.py", "src/agents/delegation.py", "src/gateway/collaboration.py"]:
        tree = ast.parse((root / file).read_text())
        assert not any(isinstance(node, ast.ImportFrom) and node.module == "src.agents.main_agent"
                       for node in ast.walk(tree)), file


def test_native_schema_preserves_task_arrays_and_integer_rounds(tmp_path):
    service, by = tools(tmp_path)
    schema = {entry["function"]["name"]: entry["function"]["parameters"]["properties"]
              for entry in MainAgent(list(by.values()), llm=EchoLLM())._tools_schema()}
    assert schema["task"]["acceptance"]["type"] == "array"
    assert schema["research_parallel"]["tasks"]["items"] == {"type": "string"}
    assert schema["task_review"]["round"]["type"] == "integer"
    assert schema["task_followup"]["round"]["type"] == "integer"


def test_review_projection_and_live_updates_follow_durable_state(tmp_path):
    updates = []
    service = CollaborationService(str(tmp_path), "sid-parent", on_update=lambda task: updates.append((task.status, task.collaboration["review"])))
    task = service.create("研究入口")
    service.start(task.id, 1)
    done = service.finish(task.id, 1, "入口分析")
    queue = build_decision_queue(tasks=[done])
    assert len(queue) == 1 and queue[0]["title"] == "委派任务待验收"
    view = task_session_view(str(tmp_path), done)
    assert view["can_pause"] is False and "验收" in view["handoff"]["next_action"]
    accepted = service.review(task.id, 1, "accept", "已审查入口分析")
    assert build_decision_queue(tasks=[accepted]) == []
    assert updates == [("queued", "not_submitted"), ("running", "not_submitted"),
                       ("done", "pending"), ("done", "accepted")]


def test_transport_failure_does_not_lose_persisted_task(tmp_path):
    def broken(task):
        raise RuntimeError("client disconnected")
    service = CollaborationService(str(tmp_path), "sid-parent", on_update=broken)
    task = service.create("研究入口")
    assert service.get(task.id).status == "queued"
