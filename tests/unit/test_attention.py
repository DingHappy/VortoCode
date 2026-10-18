"""「等你处理」清单：决策队列 + 任务单，同一作业只看最新一次运行，按紧急程度排序。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.workflow.tasks import assistant_dispatch as dispatch
from src.workflow.tasks.attention import attention_items
from src.workflow.tasks.decisions import build_decision_queue


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "resident_projects.json"
    path.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": "/srv/VortoCode", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(path))


def _run(run_id, status, updated, command="python -m src.scheduling.relay_duty", kind="cron", job=""):
    return SimpleNamespace(id=run_id, status=status, kind=kind, command=command, error="x", output="",
                           updated=updated, created=updated, sandbox={"cron_job": job} if job else {})


def test_a_later_success_resolves_earlier_failures_of_the_same_job():
    runs = [_run("r1", "failed", "2026-10-12T02:00"), _run("r2", "failed", "2026-10-13T02:00"),
            _run("r3", "done", "2026-10-15T02:00"),
            _run("t1", "failed", "2026-10-14T10:00", command="pytest -q", kind="terminal")]
    assert [item["id"] for item in build_decision_queue(runs=runs)] == ["run:t1"]   # 巡检已恢复，测试仍红
    runs.append(_run("r4", "failed", "2026-10-16T02:00"))
    assert {item["id"] for item in build_decision_queue(runs=runs)} == {"run:t1", "run:r4"}


def test_a_fixed_command_path_still_resolves_the_same_job():
    """真机 2026-10-18：修模块路径改掉了 command，按 command 分组会让修好前的失败一直挂着。"""
    runs = [_run("r1", "failed", "2026-10-14T02:00", command="python -m src.gateway.relay_duty", job="relay_duty"),
            _run("r2", "done", "2026-10-15T02:00", command="python -m src.scheduling.relay_duty", job="relay_duty")]
    assert build_decision_queue(runs=runs) == []


def test_attention_merges_dispatches_with_the_decision_queue_most_urgent_first(tmp_path):
    from src.workflow.workbench.runs import CommandRun, RunLedger
    root = str(tmp_path)
    proposed = dispatch.propose(root, project="VortoCode", title="修登录", goal="g")
    asking = dispatch.propose(root, project="VortoCode", title="改文档", goal="g")
    dispatch._update(root, asking["id"], lambda item: item.update(status="waiting"))
    done = dispatch.propose(root, project="VortoCode", title="已完成的", goal="g")
    dispatch._update(root, done["id"], lambda item: item.update(status="done"))
    run = CommandRun.new("pytest -q", "terminal")
    run.status = "failed"
    RunLedger(root).save(run)

    items = attention_items(root)
    titles = [item["title"] for item in items]
    assert titles[-1] == "任务单等你确认下发 · 修登录"                         # medium 排在 high 之后
    assert set(titles[:-1]) == {"任务在等你回答 · 改文档", "测试或命令运行失败"}
    assert not any("已完成的" in title for title in titles)
    assert proposed["id"] in items[-1]["id"]


def test_a_broken_ledger_does_not_hide_the_others(tmp_path):
    root = str(tmp_path)
    dispatch.propose(root, project="VortoCode", title="修登录", goal="g")
    runs_dir = tmp_path / ".vortocode" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "run-broken.json").write_text("{not json")
    assert [item["kind"] for item in attention_items(root)] == ["dispatch"]
