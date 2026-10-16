"""Actual delegation, typed forecasts, durable claims and measured model profiles."""
import asyncio
import json

import pytest

from src.agents.delegation import build_research_tools
from src.agents.tools.model_assessment import build_model_assessment_tools, parse_forecasts
from src.llm.client import LLMClient, LLMConfig, _account, new_usage, usage_meter
from src.llm.observations import observation_scope, summarize
from src.llm.providers import request_target_view, reset_cache
from src.workflow.tasks.collaboration import CollaborationService
from src.workflow.tasks.errors import CollaborationConflict
from src.workflow.tasks.model_evidence import continuation_economics, fast_decision


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://models.example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-model-key")
    monkeypatch.setenv("DEFAULT_MODEL", "small")
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "research=default:small,assess=default:judge")
    monkeypatch.setenv("VORTOCODE_MODEL_PRICES", json.dumps({
        "default:small": {"input_per_million": 1, "output_per_million": 2, "currency": "CNY"},
        "default:judge": {"input_per_million": 3, "output_per_million": 4, "currency": "CNY"}}))
    reset_cache()
    yield
    reset_cache()


def submitted(service, *, verdict=None, task_type="analysis", exact=True):
    task = service.create("分析示例模块", acceptance=["说明入口"], task_type=task_type)
    service.start(task.id, 1)
    config = LLMConfig(model="small")
    service.record_model_target(task.id, 1, request_target_view(LLMClient(config)))
    with observation_scope() as bucket:
        _account([{"content": "输入"}], "入口为 main", {"prompt_tokens": 100, "completion_tokens": 20} if exact else None,
                 model="small", config=config)
    metrics = {**summarize(bucket, 100, "read", complete=True), "task_type": task_type}
    task = service.finish(task.id, 1, "入口为 main", model_observation=metrics)
    if verdict:
        task = service.review(task.id, 1, verdict, "已核对示例证据")
    return task


def forecast(**updates):
    return {"meets_acceptance": .9, "retry_success": .5, "needs_stronger_model": .2,
            "criteria": [{"index": 0, "status": "met", "quote": "入口为 main"}], **updates}


def tool(service, name="task_model_assess", **kwargs):
    async def confirm(_): return True
    kwargs.setdefault("confirm", confirm)
    return next(t for t in build_model_assessment_tools(service, **kwargs) if t.name == name)


@pytest.mark.asyncio
async def test_real_worker_accounting_rounds_and_parent_acceptance(tmp_path, monkeypatch):
    calls = []
    async def chat(self, messages, **kwargs):
        calls.append(self.config.model)
        _account(messages, "入口为 main", {"prompt_tokens": 100, "completion_tokens": 20}, model=self.config.model, config=self.config)
        return {"content": "入口为 main"}
    monkeypatch.setattr(LLMClient, "chat", chat)
    service = CollaborationService(str(tmp_path), "owner")
    by = {t.name: t for t in build_research_tools(str(tmp_path), collaboration=service)}
    first = json.loads(await by["task"].handler({"description": "说明入口", "acceptance": ["说明入口"], "task_type": "analysis"}))
    assert first["model_observations"][0]["tokens"] == 120
    assert first["model_observations"][0]["cost_estimate"] == pytest.approx(.00014)
    service.review(first["task_id"], 1, "rework", "补充说明")
    await by["task_followup"].handler({"task_id": first["task_id"], "round": 1, "message": "补充说明"})
    service.review(first["task_id"], 2, "accept", "核对完成")
    profile = service.model_profiles()["profiles"][0]
    assert (profile["rounds"], profile["rework"], profile["accepted"], profile["repeated_rounds"]) == (2, 1, 1, 1)
    assert profile["cost_per_accepted"]["CNY"] == pytest.approx(.00028)
    assert calls == ["small", "small"]
    assert "synthetic-model-key" not in json.dumps(service.get(first["task_id"]).to_dict())


@pytest.mark.asyncio
async def test_independent_judge_is_metered_not_acceptance_and_not_repeated(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner")
    task = submitted(service)
    called = []
    async def chat(self, messages, **kwargs):
        called.append(self.config.model)
        assert self.config.max_retries == 0 and kwargs["max_tokens"] == 800 and kwargs["tools"] is None
        _account(messages, "判定", {"prompt_tokens": 50, "completion_tokens": 10}, model=self.config.model, config=self.config)
        return {"content": json.dumps(forecast())}
    monkeypatch.setattr(LLMClient, "chat", chat)
    assess = tool(service)
    first = json.loads(await assess.handler({"task_id": task.id, "round": 1}))
    second = json.loads(await assess.handler({"task_id": task.id, "round": 1}))
    assert first["revision"] == second["revision"] and first["forecasts"] == second["forecasts"]
    assert first["judge_observation"] == second["judge_observation"] and called == ["judge"]
    assert first["action"] == "review_recommended" and first["probability_calibration"] == "unvalidated"
    assert first["judge_observation"]["cost_estimate"] == pytest.approx(.00019)
    assert service.get(task.id).collaboration["review"] == "pending"
    service.review(task.id, 1, "accept", "人工核对入口")
    profile = service.model_profiles()["profiles"][0]
    assert profile["calibration"]["brier_score"] == pytest.approx(.01)
    assert profile["judge_cost_estimates"]["CNY"] == pytest.approx(.00019)
    assert service.model_profiles()["judge_calibration"] == [{"provider": "default", "model": "judge", "samples": 1,
                                                             "brier_score": pytest.approx(.01), "reference": "parent_review_not_ground_truth"}]
    # A cheap accepted-state check does not erase the original judge receipt.
    assert json.loads(await assess.handler({"task_id": task.id, "round": 1}))["action"] == "complete"
    assert service.model_profiles()["profiles"][0]["calibration"]["samples"] == 1


@pytest.mark.asyncio
async def test_concurrent_claim_and_late_result_keep_cost_without_applying_advice(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner")
    task = submitted(service)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def chat(self, messages, **kwargs):
        calls.append(1); entered.set(); await release.wait()
        _account(messages, "判定", {"prompt_tokens": 5, "completion_tokens": 1}, model=self.config.model, config=self.config)
        return {"content": json.dumps(forecast())}
    monkeypatch.setattr(LLMClient, "chat", chat)
    assess = tool(service)
    first = asyncio.create_task(assess.handler({"task_id": task.id, "round": 1}))
    await entered.wait()
    duplicate = json.loads(await assess.handler({"task_id": task.id, "round": 1}))
    assert duplicate["state"] == "running"
    service.review(task.id, 1, "rework", "说明不足")
    service.followup(task.id, 1, "补充证据")
    release.set()
    result = json.loads(await first)
    assert result["stale"] and result["action"] == "evidence_needed" and calls == [1]
    assert service.get(task.id).collaboration["round"] == 2
    profile = service.model_profiles()["profiles"][0]
    assert profile["judge_cost_estimates"]["CNY"] > 0 and profile["calibration"]["samples"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("roles,allow", [("research=default:small", True), ("assess=missing:judge", True), ("assess=default:judge", False)])
async def test_unavailable_or_injected_judge_never_falls_back_to_paid_default(tmp_path, monkeypatch, roles, allow):
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", roles)
    async def forbidden(*args, **kwargs): pytest.fail("must not send")
    monkeypatch.setattr(LLMClient, "chat", forbidden)
    service = CollaborationService(str(tmp_path), "owner"); task = submitted(service)
    result = json.loads(await tool(service, allow_judge=allow).handler({"task_id": task.id, "round": 1}))
    assert result["state"] == "not_configured"
    assert not service.get(task.id).collaboration.get("model_assessment_attempts")


@pytest.mark.asyncio
async def test_judge_error_and_unknown_billing_do_not_retry_or_make_zero_cost(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner"); task = submitted(service)
    calls = []
    async def chat(*args, **kwargs): calls.append(1); raise RuntimeError("sensitive provider payload")
    monkeypatch.setattr(LLMClient, "chat", chat)
    assess = tool(service)
    for _ in range(2):
        result = json.loads(await assess.handler({"task_id": task.id, "round": 1}))
        assert result["action"] == "evidence_needed" and result["judge_observation"]["cost_estimate"] is None
        assert "sensitive provider payload" not in json.dumps(result)
    assert len(calls) == 1 and service.model_profiles()["profiles"][0]["unknown_judge_cost_rounds"] == 1


@pytest.mark.asyncio
async def test_cancelled_judge_keeps_unknown_receipt_and_does_not_replay(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner"); task = submitted(service)
    async def chat(*args, **kwargs): raise asyncio.CancelledError
    monkeypatch.setattr(LLMClient, "chat", chat)
    assess = tool(service)
    with pytest.raises(asyncio.CancelledError): await assess.handler({"task_id": task.id, "round": 1})
    result = json.loads(await assess.handler({"task_id": task.id, "round": 1}))
    assert result["source"] == "judge_interrupted" and result["judge_observation"]["cost_estimate"] is None


@pytest.mark.parametrize("updates", [
    {"retry_success": float("nan")}, {"meets_acceptance": True}, {"needs_stronger_model": 1.01},
    {"criteria": []}, {"criteria": [{"index": 0, "status": "met", "quote": "并不存在的测试证据"}]},
    {"criteria": [{"index": 0, "status": "met", "quote": ""}]}, {"action": "accept"},
])
def test_forecasts_reject_untyped_or_unbound_evidence(updates):
    with pytest.raises(ValueError): parse_forecasts(json.dumps(forecast(**updates)), {"acceptance": ["说明入口"], "result": "入口为 main"})


def test_fast_rules_require_eight_distinct_priced_reviewed_tasks_and_same_type(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner")
    current = submitted(service)
    for _ in range(7): submitted(service, verdict="accept")
    data = service.model_assessment_snapshot(current.id, 1)
    assert fast_decision(data, service.model_profiles(exclude=current.id))["action"] == "judge_needed"
    submitted(service, verdict="accept")
    history = service.model_profiles(exclude=current.id)
    assert fast_decision(data, history)["action"] == "continue_recommended"
    data["task_type"] = "translation"
    assert fast_decision(data, history)["action"] == "judge_needed"
    data["task_type"] = "analysis"; data["round"] = 3
    assert fast_decision(data, history)["action"] == "stop"
    assert CollaborationService(str(tmp_path), "other-owner").model_profiles()["profiles"] == []


def test_missing_usage_and_unknown_provider_price_remain_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_MODEL_PRICES", "{}")
    service = CollaborationService(str(tmp_path), "owner")
    task = submitted(service, exact=False)
    observation = task.collaboration["model_observations"][0]
    assert observation["cost_estimate"] is None and observation["components"][0]["estimated_calls"] == 1
    profile = service.model_profiles()["profiles"][0]
    assert profile["unknown_cost_rounds"] == 1 and profile["cost_per_accepted"] == {}
    assert summarize({}, 1, "read", complete=False)["tokens"] is None


@pytest.mark.asyncio
async def test_parallel_worker_observation_scopes_do_not_cross_count_or_break_budget():
    budget = new_usage()
    async def worker(n):
        with observation_scope() as meter:
            await asyncio.sleep(0)
            _account([], "", {"prompt_tokens": n, "completion_tokens": 1}, model="small", config=LLMConfig(model="small"))
        return summarize(meter, 1, "read", complete=True)["tokens"]
    with usage_meter(budget):
        assert await asyncio.gather(worker(10), worker(20)) == [11, 21]
    assert budget["total_tokens"] == 32


@pytest.mark.asyncio
async def test_owner_round_and_caller_fields_cannot_override_judge_policy(tmp_path):
    service = CollaborationService(str(tmp_path), "owner"); task = submitted(service)
    with pytest.raises(CollaborationConflict):
        await tool(CollaborationService(str(tmp_path), "other")).handler({"task_id": task.id, "round": 1})
    with pytest.raises(CollaborationConflict): await tool(service).handler({"task_id": task.id, "round": 2})
    with pytest.raises(ValueError): await tool(service).handler({"task_id": task.id, "round": 1, "model": "expensive"})


@pytest.mark.asyncio
async def test_claim_must_persist_before_judge_call(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner"); task = submitted(service)
    monkeypatch.setattr(service.ledger, "save", lambda task: False)
    async def forbidden(*args, **kwargs): pytest.fail("claim failed; must not send")
    monkeypatch.setattr(LLMClient, "chat", forbidden)
    with pytest.raises(OSError): await tool(service).handler({"task_id": task.id, "round": 1})


@pytest.mark.asyncio
async def test_changed_review_does_not_buy_a_second_judgment_in_same_round(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner"); task = submitted(service)
    calls = []
    async def chat(*args, **kwargs): calls.append(1); return {"content": json.dumps(forecast())}
    monkeypatch.setattr(LLMClient, "chat", chat)
    assess = tool(service)
    await assess.handler({"task_id": task.id, "round": 1})
    service.review(task.id, 1, "rework", "需补充证据")
    result = json.loads(await assess.handler({"task_id": task.id, "round": 1}))
    assert result["stale"] and result["action"] == "evidence_needed" and len(calls) == 1


@pytest.mark.asyncio
async def test_failed_worker_keeps_partial_usage_and_never_claims_zero_bill(tmp_path, monkeypatch):
    async def chat(self, messages, **kwargs):
        _account(messages, "前半段", {"prompt_tokens": 100, "completion_tokens": 20}, model=self.config.model, config=self.config)
        raise RuntimeError("下一步失败")
    monkeypatch.setattr(LLMClient, "chat", chat)
    service = CollaborationService(str(tmp_path), "owner")
    by = {t.name: t for t in build_research_tools(str(tmp_path), collaboration=service)}
    result = json.loads(await by["task"].handler({"description": "分析入口"}))
    assert result["status"] == "failed"
    metrics = result["model_observations"][0]
    assert metrics["tokens"] == 120 and metrics["cost_estimate"] is None
    assert metrics["components"][0]["cost_estimate"] > 0 and metrics["usage_evidence"] == "partial_or_unknown"


def test_partial_scan_or_unknown_price_cannot_enable_stable_shortcut(tmp_path, monkeypatch):
    from dataclasses import replace
    service = CollaborationService(str(tmp_path), "owner"); current = submitted(service)
    for _ in range(8): submitted(service, verdict="accept")
    full = service.ledger.scan()
    monkeypatch.setattr(service.ledger, "scan", lambda **kwargs: replace(full, truncated=True))
    data = service.model_assessment_snapshot(current.id, 1)
    assert fast_decision(data, service.model_profiles(exclude=current.id))["action"] == "judge_needed"


def test_invalid_prices_do_not_become_free_and_price_snapshots_survive_config_changes(monkeypatch):
    with observation_scope() as bucket:
        _account([], "", {"prompt_tokens": 100, "completion_tokens": 20}, model="small", config=LLMClient(LLMConfig(model="small")).config)
    first = summarize(bucket, 1, "read", complete=True)
    monkeypatch.setenv("VORTOCODE_MODEL_PRICES", '{"default:small":{"currency":"USD","input_per_million":true,"output_per_million":0}}')
    assert summarize(bucket, 1, "read", complete=True)["cost_estimate"] is None
    assert first["components"][0]["tariff"]["currency"] == "CNY"


@pytest.mark.asyncio
async def test_configured_judge_without_confirmation_cannot_send_or_claim(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner"); task = submitted(service)
    async def forbidden(*args, **kwargs): pytest.fail("confirmation missing; must not send")
    monkeypatch.setattr(LLMClient, "chat", forbidden)
    result = json.loads(await tool(service, confirm=None).handler({"task_id": task.id, "round": 1}))
    assert result["state"] == "permission_required"
    assert not service.get(task.id).collaboration.get("model_assessment_attempts")


def test_blocked_worker_retains_spent_tokens_before_answer(tmp_path):
    service = CollaborationService(str(tmp_path), "owner")
    task = service.create("分析示例", dispatch={"source": "api"})
    service.start(task.id, 1)
    config = LLMClient(LLMConfig(model="small")).config
    service.record_model_target(task.id, 1, request_target_view(LLMClient(config)))
    with observation_scope() as bucket:
        _account([], "", {"prompt_tokens": 10, "completion_tokens": 2}, model="small", config=config)
    service.ask_question(task.id, 1, "请选择目标模块")
    service.record_model_progress(task.id, 1, {**summarize(bucket, 50, "read", complete=False), "task_type": "analysis"})
    profile = service.model_profiles()["profiles"][0]
    assert profile["median_tokens"] == 12 and profile["pending_or_unknown"] == 1
    with pytest.raises(CollaborationConflict): service.record_model_progress(task.id, 1, summarize(bucket, 50, "read", complete=False))


def test_near_zero_success_forecast_cannot_emit_infinite_money(tmp_path):
    service = CollaborationService(str(tmp_path), "owner")
    submitted(service, verdict="accept"); current = submitted(service)
    data = service.model_assessment_snapshot(current.id, 1)
    economics = continuation_economics(data, service.model_profiles(exclude=current.id), 1e-320)
    assert economics["estimates"]["CNY"]["cost_per_predicted_success"] is None
    json.dumps(economics, allow_nan=False)
