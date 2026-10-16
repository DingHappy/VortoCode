"""Model choice advice uses measured evidence, actual configuration and all fees."""
import json
import os
from dataclasses import replace

import pytest

from src.agents.tools.model_assessment import build_model_assessment_tools
from src.llm.client import LLMClient, LLMConfig, _account
from src.llm.observations import configured_tariff, observation_scope, summarize
from src.llm.providers import configured_worker_targets, request_target_view, reset_cache
from src.workflow.tasks.collaboration import CollaborationService
from src.workflow.tasks.errors import CollaborationConflict
from src.workflow.tasks.model_comparison import compare_models
from src.workflow.tasks.model_evidence import fast_decision, revision


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://models.example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-model-key")
    monkeypatch.setenv("DEFAULT_MODEL", "large")
    monkeypatch.setenv("VORTOCODE_MODEL_CHOICES", "small,large")
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "research=default:small,implement=default:large,assess=default:judge")
    monkeypatch.setenv("VORTOCODE_MODEL_PRICES", json.dumps({
        "default:small": {"input_per_million": .1, "output_per_million": .2, "currency": "CNY"},
        "default:large": {"input_per_million": 1, "output_per_million": 2, "currency": "CNY"},
        "default:judge": {"input_per_million": 1, "output_per_million": 2, "currency": "CNY"}}))
    reset_cache()
    yield
    reset_cache()


def task(service, model="large", *, accept=True, exact=True, category="read", task_type="analysis", elapsed=None):
    item = service.create("分析示例模块", acceptance=["说明入口"], task_type=task_type)
    service.start(item.id, 1)
    client = LLMClient(LLMConfig(model=model))
    service.record_model_target(item.id, 1, request_target_view(client))
    with observation_scope() as meter:
        _account([], "说明入口", {"prompt_tokens": 100, "completion_tokens": 20} if exact else None,
                 model=model, config=client.config)
    metrics = {**summarize(meter, elapsed if elapsed is not None else (100 if model == "small" else 1000), category, complete=True),
               "task_type": task_type}
    item = service.finish(item.id, 1, "入口为 main", model_observation=metrics)
    return service.review(item.id, 1, "accept", "已核对示例") if accept else item


def comparison(service, current):
    data = service.model_assessment_snapshot(current.id, 1)
    history = service.model_profiles(exclude=current.id)
    prices = {(b["provider"], b["model"]): configured_tariff(b["provider"], b["model"])
              for p in history["profiles"] for b in p["price_bases"]}
    return compare_models(data, history, configured_worker_targets(), prices)


def populate(service, **small_kwargs):
    for _ in range(8):
        task(service, "large"); task(service, "small", **small_kwargs)
    return task(service, accept=False)


def test_cost_quality_and_latency_frontier_uses_same_scope_and_excludes_current(tmp_path):
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service)
    result = comparison(service, current)
    assert result["status"] == "candidate_for_review"
    assert result["recommended_target"] == {"provider": "default", "model": "small"}
    small, large = [next(r for r in result["candidates"] if r["model"] == model) for model in ("small", "large")]
    assert small["tasks"] == large["tasks"] == 8
    assert small["total_cost_per_accepted"] == pytest.approx(.000014)
    assert large["total_cost_per_accepted"] == pytest.approx(.00014)
    assert small["on_frontier"] and not large["on_frontier"]
    assert service.get(current.id).collaboration["review"] == "pending" and not result["automatic_execution"]


@pytest.mark.parametrize("options,reason", [
    ({"exact": False}, "usage_not_exact"), ({"category": "dev"}, "no_matching_task_history"),
    ({"task_type": "translation"}, "no_matching_task_history"),
])
def test_unknown_or_different_task_conditions_cannot_win(tmp_path, options, reason):
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service, **options)
    result = comparison(service, current)
    row = next(r for r in result["candidates"] if r["model"] == "small")
    assert reason in row["missing"] and result["recommended_target"] is None


def test_latency_tradeoff_does_not_become_a_cheapest_model_order(tmp_path):
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service, elapsed=5000)
    result = comparison(service, current)
    assert result["recommended_target"] is None
    assert all(r["on_frontier"] for r in result["candidates"])


def test_changing_price_blocks_ranking_without_rewriting_historical_cost(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service)
    prices = json.loads(os.environ["VORTOCODE_MODEL_PRICES"])
    prices["default:small"]["input_per_million"] = 50
    monkeypatch.setenv("VORTOCODE_MODEL_PRICES", json.dumps(prices))
    result = comparison(service, current)
    small = next(r for r in result["candidates"] if r["model"] == "small")
    assert "pricing_missing_or_changed" in small["missing"] and not result["recommended_target"]
    assert small["worker_cost_per_accepted"] == pytest.approx(.000014)


def judge_fee(service, item, *, amount=None, currency="CNY"):
    data = service.model_assessment_snapshot(item.id, 1)
    token = revision(data)
    service.claim_model_assessment(item.id, 1, token, {"action": "judge_needed"})
    service.finish_model_assessment(item.id, 1, token, {
        "action": "evidence_needed", "judge_observation": {"cost_estimate": amount, "currency": currency,
            "components": [{"provider": "default", "model": "judge", "tariff": configured_tariff("default", "judge")}]}})


def test_judge_fees_can_erase_apparent_cheap_worker_advantage(tmp_path):
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service)
    cheap = next(t for t in service.list() if t.collaboration["model_target"]["model"] == "small")
    judge_fee(service, cheap, amount=.02)
    result = comparison(service, current)
    small = next(r for r in result["candidates"] if r["model"] == "small")
    assert small["judge_cost_per_accepted"] == pytest.approx(.0025)
    assert small["total_cost_per_accepted"] == pytest.approx(.002514)
    assert result["recommended_target"] is None


def test_changed_judge_tariff_is_also_a_comparison_gap(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service)
    cheap = next(t for t in service.list() if t.collaboration["model_target"]["model"] == "small")
    judge_fee(service, cheap, amount=.000001)
    prices = json.loads(os.environ["VORTOCODE_MODEL_PRICES"])
    prices["default:judge"]["input_per_million"] = 100
    monkeypatch.setenv("VORTOCODE_MODEL_PRICES", json.dumps(prices))
    result = comparison(service, current)
    small = next(r for r in result["candidates"] if r["model"] == "small")
    assert "pricing_missing_or_changed" in small["missing"] and result["recommended_target"] is None


@pytest.mark.parametrize("amount,currency", [(None, "CNY"), (.01, "USD")])
def test_unknown_or_other_currency_judge_fee_is_not_zero(tmp_path, amount, currency):
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service)
    cheap = next(t for t in service.list() if t.collaboration["model_target"]["model"] == "small")
    judge_fee(service, cheap, amount=amount, currency=currency)
    result = comparison(service, current)
    small = next(r for r in result["candidates"] if r["model"] == "small")
    assert "judge_cost_incomplete_or_mixed_currency" in small["missing"]
    assert small["total_cost_per_accepted"] is None and result["recommended_target"] is None


def test_mixed_worker_currencies_are_not_converted_or_ranked(tmp_path, monkeypatch):
    prices = json.loads(os.environ["VORTOCODE_MODEL_PRICES"])
    prices["default:small"]["currency"] = "USD"
    monkeypatch.setenv("VORTOCODE_MODEL_PRICES", json.dumps(prices))
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service)
    assert comparison(service, current)["recommended_target"] is None


def test_partial_scan_and_removed_model_configuration_do_not_recommend(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service)
    full = service.ledger.scan()
    monkeypatch.setattr(service.ledger, "scan", lambda **kwargs: replace(full, truncated=True))
    result = comparison(service, current)
    assert not result["coverage_complete"] and not result["recommended_target"]
    monkeypatch.setenv("VORTOCODE_MODEL_CHOICES", "large")
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "implement=default:large,assess=default:judge")
    assert {r["model"] for r in comparison(service, current)["candidates"]} == {"large"}


def test_sparse_evidence_and_explicit_free_prices(tmp_path, monkeypatch):
    service = CollaborationService(str(tmp_path), "owner")
    for _ in range(7): task(service, "small")
    for _ in range(8): task(service, "large")
    current = task(service, accept=False)
    result = comparison(service, current)
    assert "too_few_tasks" in next(r for r in result["candidates"] if r["model"] == "small")["missing"]
    # A new repository with explicitly configured zero prices is genuinely zero estimate.
    prices = json.loads(os.environ["VORTOCODE_MODEL_PRICES"])
    prices["default:small"].update(input_per_million=0, output_per_million=0)
    monkeypatch.setenv("VORTOCODE_MODEL_PRICES", json.dumps(prices))
    other = CollaborationService(str(tmp_path / "other"), "owner"); current = populate(other)
    assert comparison(other, current)["recommended_target"]["model"] == "small"


@pytest.mark.asyncio
async def test_cost_comparison_works_with_judge_disabled_and_never_sends_a_request(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_MODEL_ROLES", "research=default:small,implement=default:large")
    service = CollaborationService(str(tmp_path), "owner"); current = populate(service)
    async def forbidden(*args, **kwargs): pytest.fail("deterministic comparison must not send")
    monkeypatch.setattr(LLMClient, "chat", forbidden)
    assess = next(t for t in build_model_assessment_tools(service) if t.name == "task_model_assess")
    result = json.loads(await assess.handler({"task_id": current.id, "round": 1}))
    saved = service.get(current.id).collaboration["model_comparison"]
    assert result["model_comparison"] == saved
    assert saved["recommended_target"]["model"] == "small"
    assert not service.get(current.id).collaboration.get("model_assessment_attempts")


def test_comparison_write_is_bound_to_owner_round_and_result(tmp_path):
    service = CollaborationService(str(tmp_path), "owner"); current = task(service, accept=False)
    data = service.model_assessment_snapshot(current.id, 1); report = comparison(service, current)
    token = revision(data)
    with pytest.raises(CollaborationConflict):
        CollaborationService(str(tmp_path), "other").record_model_comparison(current.id, 1, token, report)
    with pytest.raises(CollaborationConflict): service.record_model_comparison(current.id, 2, token, report)
    service.review(current.id, 1, "accept", "证据已更新")
    with pytest.raises(CollaborationConflict): service.record_model_comparison(current.id, 1, token, report)


def test_failed_dependencies_stop_before_buying_judgment(tmp_path):
    service = CollaborationService(str(tmp_path), "owner"); current = task(service, accept=False)
    data = service.model_assessment_snapshot(current.id, 1)
    data["status"] = "failed"; data["dependencies"] = {"resolution": "failed"}
    assert fast_decision(data, service.model_profiles())["action"] == "evidence_needed"
    result = compare_models(data, service.model_profiles(), configured_worker_targets(), {})
    assert result["status"] == "not_actionable" and result["recommended_target"] is None


def test_candidate_configuration_is_bounded_secret_free_and_has_no_assessor_only_model(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MODEL_CHOICES", ",".join(f"model-{n}" for n in range(80)))
    result = configured_worker_targets()
    assert len(result["targets"]) == 64 and result["truncated"]
    assert "judge" not in {r["model"] for r in result["targets"]}
    assert "https" not in json.dumps(result) and "synthetic-model-key" not in json.dumps(result)


def test_keyless_default_loopback_uses_same_target_identity_as_actual_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:9123/v1")
    monkeypatch.delenv("OPENAI_API_KEY")
    target = request_target_view(LLMClient())
    assert target["provider"] == "default"
    assert {"provider": target["provider"], "model": target["model"]} in configured_worker_targets()["targets"]
