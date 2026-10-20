"""派活成绩：一串重试算一件事；只数窗口内下发过的；周日晚报附本周成绩。全部离线。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import assistant_watch as watch
from src.workflow.tasks import assistant_dispatch as dispatch
from src.workflow.tasks.dispatch_stats import dispatch_stats, stats_text

NOW = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)          # 周日


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "resident_projects.json"
    path.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": "/srv/VortoCode", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(path))


def _ticket(root, *, status, hours_ago=5, minutes=40, retry_of="", **fields):
    entry = dispatch.propose(root, project="VortoCode", title="t", goal="g")
    sent = NOW - timedelta(hours=hours_ago)
    dispatch._update(root, entry["id"], lambda item: item.update(
        status=status, sent_at=sent.isoformat(), finished_at=(sent + timedelta(minutes=minutes)).isoformat(),
        retry_of=retry_of, attempt=2 if retry_of else 1, **fields))
    return entry["id"]


def _seed(root):
    _ticket(root, status="done", minutes=30, pr_url="https://x/pull/1", answers=[{"q": 1}, {"q": 2}],
            outcome={"kind": "acceptance", "criteria": [{"verdict": "met"}, {"verdict": "unknown"}]})
    first = _ticket(root, status="failed")
    _ticket(root, status="done", minutes=50, retry_of=first, goal_confirmed_at=NOW.isoformat())
    _ticket(root, status="failed")
    _ticket(root, status="running", minutes=0)
    _ticket(root, status="done", hours_ago=24 * 40)                      # 窗口外
    old = dispatch.propose(root, project="VortoCode", title="没下发", goal="g")
    dispatch.cancel(root, old["id"])


def test_counts_things_not_just_tickets(tmp_path):
    root = str(tmp_path)
    _seed(root)
    stats = dispatch_stats(root, NOW, 30)
    assert stats["sent"] == 5 and stats["things"] == 4 and stats["things_finished"] == 3
    assert stats["things_succeeded"] == 2 and stats["first_try"] == 1 and stats["retried_to_success"] == 1
    assert stats["in_flight"] == 1 and stats["median_minutes"] == 40 and stats["asked"] == 1 and stats["answers"] == 2
    assert (stats["criteria_met"], stats["criteria_checked"], stats["prs"], stats["goal_confirmed"]) == (1, 2, 1, 1)
    assert stats["withdrawn"] == 1
    text = stats_text(stats)
    assert "做成 2/3 件（67%）" in text and "第一次就成 1 件、重试后成 1 件" in text and "中位数" in text
    assert "1/2 条对上" in text and "开了 1 个 draft PR" in text


def test_nothing_sent_is_one_line(tmp_path):
    assert stats_text(dispatch_stats(str(tmp_path), NOW, 7)) == "最近 7 天派活：没有下发过任务单。"


async def test_sunday_evening_report_carries_the_week(tmp_path, monkeypatch):
    root = str(tmp_path)
    _seed(root)

    async def body(*_a, **_k):
        return "晚报正文"
    monkeypatch.setattr(watch, "_compose_body", body)
    sunday = await watch.compose_report(root, NOW, "晚报")
    assert sunday.startswith("晚报正文") and "📊 本周派活：下发 5 张" in sunday
    assert "本周派活" not in await watch.compose_report(root, NOW - timedelta(days=1), "晚报")


async def test_owner_can_ask_for_the_numbers(tmp_path):
    root = str(tmp_path)
    tools = {tool.name: tool for tool in dispatch.build_dispatch_tools(root)}
    assert tools["dispatch_stats"].read_only
    assert "没有下发过任务单" in await tools["dispatch_stats"].handler({"days": "abc"})
