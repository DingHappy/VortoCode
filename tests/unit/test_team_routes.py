"""任务小组的 Desktop 接口（ADR-014 S3）：视图由代码拼，主人亲手点的验收 / 回话 / 停止走领域规则并记审计。"""
import pytest
from fastapi import HTTPException

from src.scheduling import team_runner
from src.web.routers import teams
from src.workflow.tasks.team import TeamService

MEMBERS = [{"name": "研究员", "role": "web-researcher"}, {"name": "审查员", "role": "reviewer"}]


class FakeRunner:
    def __init__(self, root):
        self.service = TeamService(root)
        self.woken, self.stopped = [], []

    def wake(self, root_id, event):
        self.woken.append((root_id, event))

    def stop(self, root_id, reason):
        self.stopped.append(root_id)
        root, _pending = self.service.stop(root_id, reason)
        return root


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    audits = []
    monkeypatch.setattr("src.gateway.audit.record_decision_audit", lambda *a, **k: audits.append(k))
    runner = FakeRunner(str(tmp_path))
    team_runner.set_team_runner(runner)
    yield runner, audits
    team_runner.set_team_runner(None)


async def test_list_and_detail_show_results_delivery_and_taint(setup):
    runner, _ = setup
    service = runner.service
    root = service.create("sid-main", objective="比较方案", acceptance=["给推荐"], members=MEMBERS)
    child = service.assign(root.id, "研究员", "查价格", "价格表")
    work = service.collaboration(service.get(root.id))
    work.start(child.id, 1)
    work.finish(child.id, 1, "A 10 元，来源 https://a.example", tainted=True)
    listed = (await teams.list_teams())["teams"]
    assert [item["id"] for item in listed] == [root.id] and listed[0]["status"] == "active"
    detail = await teams.team_detail(root.id)
    assert detail["acceptance"] == ["给推荐"] and detail["project"] is None
    [row] = detail["children"]
    assert row["result"].startswith("A 10 元") and row["request"] == "查价格" and row["tainted"] is True
    assert detail["tainted"] is True and detail["members"][0]["engine"] == "vortocode"
    assert [event["kind"] for event in detail["events"]] == ["created", "assigned"]
    with pytest.raises(HTTPException) as missing:
        await teams.team_detail("task-0000000000")
    assert missing.value.status_code == 404


async def test_owner_decisions_from_desktop_follow_the_domain_rules(setup):
    runner, audits = setup
    service = runner.service
    root = service.create("sid-main", objective="比较方案", acceptance=["给推荐"], members=MEMBERS)
    with pytest.raises(HTTPException) as early:
        await teams.decide_team(root.id, teams.TeamDecision(accept=True))
    assert early.value.status_code == 409                    # 还没交验收
    service.submit(root.id, "推荐 A", [{"criterion": 0, "evidence": "A 最便宜"}])
    with pytest.raises(HTTPException) as no_reason:
        await teams.decide_team(root.id, teams.TeamDecision(accept=False))
    assert no_reason.value.status_code == 422
    back = await teams.decide_team(root.id, teams.TeamDecision(accept=False, note="再比较一家"))
    assert back["status"] == "active" and runner.woken[-1] == (root.id, "主人退回了交付，按意见继续")
    service.ask_owner(root.id, "预算多少？")
    replied = await teams.reply_team(root.id, teams.TeamReply(text="500 以内"))
    assert replied["status"] == "active" and runner.woken[-1] == (root.id, "主人回复了")
    stopped = await teams.stop_team(root.id)
    assert stopped["status"] == "cancelled" and runner.stopped == [root.id]
    assert [audit["operation"][:4] for audit in audits] == ["退回任务", "回复任务", "停止任务"]


async def test_without_a_team_runner_writes_are_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    team_runner.set_team_runner(None)
    root = TeamService(str(tmp_path)).create("o", objective="x", acceptance=["y"], members=MEMBERS)
    with pytest.raises(HTTPException) as refused:
        await teams.stop_team(root.id)
    assert refused.value.status_code == 409
    assert (await teams.list_teams())["teams"][0]["id"] == root.id   # 读照样可以
