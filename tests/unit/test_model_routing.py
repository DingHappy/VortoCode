"""按回合模型调度：「自动」按任务形状选档；点名只接受服务端已配置的模型。"""
import asyncio

import pytest

from src.llm.routing import AUTO, clean_model_request, resolve_turn_model, route_model


@pytest.fixture
def tiers(monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "base-model")
    monkeypatch.setenv("LLM_MODEL_CHEAP", "fast-model")
    monkeypatch.delenv("LLM_MODEL_BALANCED", raising=False)
    monkeypatch.setenv("LLM_MODEL_POWERFUL", "strong-model")


def test_auto_routes_by_task_shape(tiers):
    assert route_model("你好", mode="plan").model == "fast-model"
    assert route_model("帮我看看这个函数的命名是否合适，顺便给点改进建议，主要关注可读性和一致性方面的问题。" * 2,
                       mode="plan").model == "base-model"
    # 模式不是信号：Desktop 每轮都按 build 发，简短问答仍走快速档。
    assert route_model("改个错别字", mode="build").model == "fast-model"
    assert route_model("这是什么", mode="plan", has_media=True).model == "strong-model"
    assert route_model("帮我排查一下这个内存泄漏", mode="plan").model == "strong-model"
    assert route_model("看看", mode="plan", context_count=3).model == "strong-model"


def test_unconfigured_tiers_fall_back_to_default(monkeypatch):
    for name in ("LLM_MODEL_CHEAP", "LLM_MODEL_BALANCED", "LLM_MODEL_POWERFUL", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEFAULT_MODEL", "only-model")
    assert route_model("你好", mode="plan").model == "only-model"
    assert route_model("重构", mode="build").model == "only-model"
    monkeypatch.delenv("DEFAULT_MODEL")
    assert route_model("你好", mode="plan") is None


def test_explicit_model_must_be_configured(tiers):
    assert resolve_turn_model("strong-model", "x", mode="plan").tier == "manual"
    assert resolve_turn_model("some-other-paid-model", "x", mode="plan") is None
    assert resolve_turn_model(None, "x", mode="build") is None


def test_models_listed_by_the_default_service_can_be_picked(monkeypatch, tiers):
    monkeypatch.setenv("VORTOCODE_MODEL_CHOICES", "mimo-v2.6-flash, deepseek-chat,")
    assert resolve_turn_model("deepseek-chat", "x", mode="build").model == "deepseek-chat"
    assert resolve_turn_model("not-listed", "x", mode="build") is None


def test_clean_model_request_shape():
    assert clean_model_request(" auto ") == AUTO
    assert clean_model_request("mimo-v2.6-pro") == "mimo-v2.6-pro"
    assert clean_model_request("openai/gpt-4o") == "openai/gpt-4o"
    for bad in (None, 3, "", "a b", "x" * 200, "-leading", "模型"):
        assert clean_model_request(bad) is None


class _ModelAgent:
    def __init__(self):
        self._on_plan = None
        self.plan = []
        self.models = []

    def set_model(self, model):
        self.models.append(model)

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None, reasoning_cb=None):
        emit("ok")


async def _run(message):
    from tests.unit.test_web_agent import _FakeWS, _cleanup, _inject_session
    from src.web.routers import realtime
    ws = _FakeWS(sid=f"route-{message.get('rid')}")
    agent = _ModelAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", **message})
        task = realtime._WS_AGENT_TASKS.get(realtime._session_key(ws))
        if task is not None:
            await asyncio.wait_for(task, 5)
        return agent.models, [m for m in ws.sent if m.get("type") == "agent_phase" and m.get("phase") == "routing"]
    finally:
        _cleanup(ws)


async def test_turn_applies_auto_choice_and_reports_it(monkeypatch, tiers):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    models, routing = await _run({"text": "实现一个新的登录流程", "mode": "build", "rid": "r1", "model": "auto"})
    # 「实现」命中复杂任务提示词，走强力档（与 mode 无关）
    assert models == ["strong-model"]
    assert routing and routing[0]["label"].startswith("使用 strong-model") and routing[0]["detail"] == "powerful"


async def test_turn_ignores_unconfigured_or_missing_model(monkeypatch, tiers):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert await _run({"text": "hi", "rid": "r2", "model": "some-other-paid-model"}) == ([], [])
    assert await _run({"text": "hi", "rid": "r3"}) == ([], [])
