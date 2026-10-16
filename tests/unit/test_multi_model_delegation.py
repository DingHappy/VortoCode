"""Real assembly/delegation with scripted model responses; no paid requests."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from src.agents.main_agent import build_subagent
from src.gateway.agent_session import build_session
from src.llm.client import LLMClient, LLMConfig
from src.llm.providers import client_for_role, request_target_view, reset_cache
from src.workflow.tasks.collaboration import CollaborationService
from src.workflow.tasks.errors import CollaborationConflict


@pytest.fixture(autouse=True)
def routing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://planner.example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-planner-key")
    monkeypatch.setenv("DEFAULT_MODEL", "planner-model")
    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "1")
    monkeypatch.setenv("VORTOCODE_PROVIDER_WORKER_BASE", "https://worker.example.test/v1")
    monkeypatch.setenv("VORTOCODE_PROVIDER_WORKER_KEY", "synthetic-worker-key")
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "research=worker:small-model,implement=worker:code-model,review=worker:review-model")
    reset_cache()
    yield
    reset_cache()


@pytest.mark.asyncio
async def test_main_dispatches_to_cheap_channel_and_keeps_its_own_model(tmp_path, monkeypatch):
    calls = []
    async def chat(self, messages, **kwargs):
        calls.append((self.config.base_url, self.config.model, self.config.api_key))
        if self.config.model == "planner-model" and not any(message["role"] == "tool" for message in messages):
            return {"content": "", "tool_calls": [{"id": "call_delegation", "name": "task", "arguments": json.dumps({"description": "只读分析示例模块"})}]}
        return {"content": "已整理分析，供主 Agent 核对。"}
    monkeypatch.setattr(LLMClient, "chat", chat)
    parent = build_session(str(tmp_path), kind="web", workspace_scope="project", llm=LLMClient())
    parent._native = True
    result = await parent.run_turn("分派只读分析并汇总", mode="plan", emit=lambda _: None)
    assert result
    assert {model for _, model, _ in calls} == {"planner-model", "small-model"}
    assert all((base, key) == (("https://worker.example.test/v1", "synthetic-worker-key") if model == "small-model"
                              else ("https://planner.example.test/v1", "synthetic-planner-key")) for base, model, key in calls)
    assert parent.current_model() == "planner-model"
    from src.workflow.tasks.tasks import TaskLedger
    tasks = [task for task in TaskLedger(str(tmp_path)).list() if task.kind == "delegation"]
    assert len(tasks) == 1 and tasks[0].collaboration["review"] == "pending"
    target = tasks[0].collaboration["model_target"]
    assert target == {"model": "small-model", "provider": "worker", "evidence": "selected_request_target", "round": 1}
    assert "synthetic-worker-key" not in json.dumps(tasks[0].to_dict())


@pytest.mark.parametrize("tools,expected", [("read", "small-model"), ("dev", "code-model"), ("deliver", "small-model")])
def test_custom_role_without_model_uses_configured_worker_tier(tmp_path, tools, expected):
    spec = SimpleNamespace(name="helper", system_prompt="示例角色", tools=tools, max_steps=2, model=None)
    sub = build_subagent(str(tmp_path), spec)
    assert sub.current_model() == expected
    assert sub._client().config.base_url == "https://worker.example.test/v1"


@pytest.mark.parametrize("model", [None, "inherit"])
def test_explicit_injected_client_is_not_bypassed_for_default_role(tmp_path, model):
    injected = SimpleNamespace(config=SimpleNamespace(model="bounded-client"))
    spec = SimpleNamespace(name="helper", system_prompt="示例角色", tools="read", max_steps=2, model=model)
    sub = build_subagent(str(tmp_path), spec, llm=injected)
    assert sub._llm is injected  # Budget/test wrappers must retain their control.


@pytest.mark.asyncio
async def test_parallel_plain_role_models_do_not_mutate_parent_or_each_other(tmp_path):
    parent = LLMClient(LLMConfig(base_url="https://planner.example.test/v1", api_key="synthetic-planner-key", model="planner-model"))
    def child(model):
        return build_subagent(str(tmp_path), SimpleNamespace(name="helper", system_prompt="fixture", tools="read", max_steps=2, model=model), llm=parent)
    first, second = child("small-a"), child("small-b")
    await asyncio.gather(asyncio.sleep(0), asyncio.sleep(0))
    assert first.current_model() == "small-a" and second.current_model() == "small-b"
    assert parent.config.model == "planner-model"
    assert first._client() is not second._client() and first._client() is not parent
    await first._client().close(); await second._client().close(); await parent.close()


def test_explicit_role_file_model_overrides_worker_default(tmp_path):
    spec = SimpleNamespace(name="helper", system_prompt="fixture", tools="read", max_steps=2, model="worker:special-model")
    sub = build_subagent(str(tmp_path), spec)
    assert sub.current_model() == "special-model"
    assert client_for_role("research").config.model == "small-model"


def test_selected_target_receipt_has_no_endpoint_or_credential():
    target = request_target_view(client_for_role("research"))
    assert target == {"model": "small-model", "provider": "worker", "evidence": "selected_request_target"}
    assert "https" not in json.dumps(target) and "key" not in json.dumps(target)


def test_model_target_is_bound_to_owner_active_round_and_is_not_acceptance(tmp_path):
    service = CollaborationService(str(tmp_path), "synthetic-owner")
    task = service.create("示例任务")
    target = request_target_view(client_for_role("research"))
    with pytest.raises(CollaborationConflict): service.record_model_target(task.id, 1, target)
    service.start(task.id, 1)
    with pytest.raises(CollaborationConflict): service.record_model_target(task.id, 2, target)
    with pytest.raises(CollaborationConflict): CollaborationService(str(tmp_path), "other-owner").record_model_target(task.id, 1, target)
    recorded = service.record_model_target(task.id, 1, target)
    assert recorded.status == "running" and recorded.collaboration["review"] == "not_submitted"
    assert service.finish(task.id, 1, "结果待验收").collaboration["model_target"]["model"] == "small-model"


@pytest.mark.asyncio
async def test_owned_role_client_is_closed_after_delegated_execution(tmp_path, monkeypatch):
    from src.agents.main_agent import build_research_tools
    from src.agents.agent_loop import MainAgent
    directory = tmp_path / ".vortocode/agents"; directory.mkdir(parents=True)
    (directory / "helper.md").write_text("---\nname: helper\nmodel: small-model\n---\n示例角色")
    parent = LLMClient()
    closed = []
    async def close(self): closed.append(self)
    async def run(self, *args, **kwargs): return "完成分析"
    monkeypatch.setattr(LLMClient, "close", close)
    monkeypatch.setattr(MainAgent, "run_turn", run)
    tool = next(tool for tool in build_research_tools(str(tmp_path), llm=parent) if tool.name == "task")
    await tool.handler({"description": "示例", "agent": "helper"})
    assert len(closed) == 1 and closed[0] is not parent and parent.config.model == "planner-model"
