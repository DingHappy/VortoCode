"""任务上下文（ADR-014 S1a）：额度、无进展判定、验收、停止、重启规则都由代码执行，不靠协调人模型。"""
import pytest

from src.workflow.tasks.errors import CollaborationConflict
from src.workflow.tasks.tasks import TaskLedger
from src.workflow.tasks.team import STALL_WAKES, TeamService, child_owner

MEMBERS = [{"name": "研究员", "role": "web-researcher"}, {"name": "审查员", "role": "reviewer", "model": "other-model"}]


def _team(tmp_path, **kwargs):
    service = TeamService(str(tmp_path))
    options = {"objective": "比较三家向量数据库并给出选型建议", "acceptance": ["列出三家的价格", "给出推荐和理由"],
               "members": MEMBERS, **kwargs}
    return service, service.create("sid-owner", **options)


def _finish(service, root, child, result="三家价格：A 10 元，B 20 元，C 30 元", *, tainted=False):
    work = service.collaboration(root)
    work.start(child.id, child.collaboration["round"])
    return work.finish(child.id, child.collaboration["round"], result, tainted=tainted)


def test_private_context_cannot_have_a_web_member_and_open_cannot_carry_private_data(tmp_path):
    service = TeamService(str(tmp_path))
    with pytest.raises(CollaborationConflict, match="联网成员"):
        service.create("sid-owner", objective="整理我的目标", acceptance=["列清单"], members=MEMBERS, mode="private")
    with pytest.raises(CollaborationConflict, match="私人资料"):
        service.create("sid-owner", objective="查资料", acceptance=["给结论"], members=MEMBERS, private_refs=["personal-1"])
    with pytest.raises(CollaborationConflict, match="未知角色"):
        service.create("sid-owner", objective="x", acceptance=["y"], members=[{"name": "开发", "role": "dev"}])


def test_assign_counts_usage_without_a_budget_and_children_belong_to_the_team(tmp_path):
    # 主人 2026-11-04：不设额度。旧的额度键（tasks 等）被忽略，派活只计数、不拦
    service, root = _team(tmp_path, limits={"tasks": 1})
    assert root.team["limits"] == {"parallel": 2}
    child = service.assign(root.id, "研究员", "查三家的公开价格", "价格表和来源链接")
    assert child.owner_session == child_owner(root.id)
    assert child.collaboration["dispatch"]["source"] == "team" and child.collaboration["dispatch"]["root"] == root.id
    root = service.get(root.id)
    assert root.team["used"]["tasks"] == 1 and root.team["used"]["steps"] == 12 and root.team["children"] == [child.id]
    for _ in range(3):
        service.assign(root.id, "审查员", "审一下", "问题清单")
    assert len(service.get(root.id).team["children"]) == 4 and service.get(root.id).team["used"]["tasks"] == 4
    with pytest.raises(CollaborationConflict, match="parallel"):
        _team(tmp_path / "p", limits={"parallel": 9})


def test_rework_charges_another_round_and_a_tainted_result_taints_the_root(tmp_path):
    service, root = _team(tmp_path)
    child = service.assign(root.id, "研究员", "查价格", "价格表")
    child = _finish(service, root, child, tainted=True)
    reworked = service.review(root.id, child.id, "rework", "缺 C 的来源链接")
    assert reworked.status == "queued" and reworked.collaboration["round"] == 2
    root = service.get(root.id)
    assert root.team["used"]["rounds"] == 2 and root.team["tainted"] is True
    assert service.view(root)["tainted"] is True


def test_coordinator_that_makes_no_progress_is_stopped_by_code(tmp_path):
    service, root = _team(tmp_path)
    service.begin_wake(root.id)
    service.assign(root.id, "研究员", "查价格", "价格表")
    assert service.end_wake(root.id).team["wake"]["idle"] == 0     # 派了活：算进展
    for _ in range(STALL_WAKES):
        service.begin_wake(root.id)
        root = service.end_wake(root.id)
    assert root.status == "waiting" and "没有新进展" in root.team["waiting_reason"]


def test_same_error_three_times_hands_back_to_the_owner_and_wakes_are_unlimited(tmp_path):
    service, root = _team(tmp_path)
    for _ in range(STALL_WAKES):
        service.begin_wake(root.id)
        root = service.end_wake(root.id, error="模型超时")
    assert root.status == "waiting" and "同一个错误" in root.team["waiting_reason"]
    service2, other = _team(tmp_path / "b", limits={"wakes": 1})          # 旧额度键：忽略
    for number in range(20):                                              # 有进展就一直能醒
        service2.begin_wake(other.id)
        service2.assign(other.id, "研究员", f"查第 {number} 家", "价格")
        service2.end_wake(other.id)
    assert service2.get(other.id).status == "active" and service2.get(other.id).team["used"]["wakes"] == 20


def test_restart_reruns_a_cut_coordinator_turn_once_then_fails(tmp_path):
    service, root = _team(tmp_path)
    service.begin_wake(root.id)
    assert service.recover_after_restart(root.id) == "rerun"
    assert service.recover_after_restart(root.id) == "failed"
    root = service.get(root.id)
    assert root.status == "failed" and "部分动作" in root.error
    assert TaskLedger(str(tmp_path)).recover_interrupted() == []    # 根不用 running，启动扫描不会误标中断


def test_submit_needs_evidence_for_every_criterion_and_only_the_owner_completes(tmp_path):
    service, root = _team(tmp_path)
    with pytest.raises(CollaborationConflict, match="还没有证据"):
        service.submit(root.id, "推荐 A", [{"criterion": 0, "evidence": "价格表见子任务"}])
    root = service.submit(root.id, "推荐 A", [{"criterion": 0, "evidence": "价格表"}, {"criterion": 1, "evidence": "A 最便宜"}])
    assert root.status == "review"
    with pytest.raises(CollaborationConflict):
        service.assign(root.id, "研究员", "再查", "x")                 # 待验收时协调人不能再派活
    root = service.decide(root.id, False, "没有考虑自建成本")
    assert root.status == "active" and service.take_inbox(root.id) == [{"from": "owner", "text": "没有考虑自建成本"}]
    service.submit(root.id, "推荐 A", [{"criterion": 0, "evidence": "价格表"}, {"criterion": 1, "evidence": "含自建"}])
    assert service.decide(root.id, True).status == "done"


def test_owner_reply_resumes_and_stop_returns_work_to_cancel(tmp_path):
    service, root = _team(tmp_path)
    assert service.ask_owner(root.id, "预算上限是多少？").status == "waiting"
    assert service.owner_reply(root.id, "每月 500 元以内").status == "active"
    first = service.assign(root.id, "研究员", "查 A", "价格")
    second = service.assign(root.id, "研究员", "查 B", "价格")
    third = service.assign(root.id, "研究员", "查 C", "价格")
    assert [task.id for task in service.runnable(service.get(root.id))] == [first.id, second.id]   # parallel=2
    root, pending = service.stop(root.id)
    assert root.status == "cancelled" and set(pending) == {first.id, second.id, third.id}
    assert service.runnable(root) == []


def test_team_children_stay_out_of_the_owner_queue_and_the_root_shows_up_when_it_needs_the_owner(tmp_path):
    from src.workflow.tasks.decisions import build_decision_queue
    service, root = _team(tmp_path)
    child = _finish(service, root, service.assign(root.id, "研究员", "查价格", "价格表"))
    tasks = TaskLedger(str(tmp_path)).list()
    assert build_decision_queue(goals=[], tasks=tasks, runs=[], dismissed=set()) == []
    service.ask_owner(root.id, "要不要包含自建方案？")
    queue = build_decision_queue(goals=[], tasks=TaskLedger(str(tmp_path)).list(), runs=[], dismissed=set())
    assert [item["title"] for item in queue] == ["任务小组需要你决定"]
    assert child.status == "done"


def test_owner_notices_cover_the_root_not_the_children_and_hide_tainted_text(tmp_path):
    from src.scheduling import task_notices
    service, root = _team(tmp_path)
    child = _finish(service, root, service.assign(root.id, "研究员", "查价格", "价格表"), tainted=True)
    assert task_notices.message(child) is None
    root = service.ask_owner(root.id, "要不要考虑 B？")
    assert task_notices.message(root) == "🧩 任务小组「比较三家向量数据库并给出选型建议」需要你决定：要不要考虑 B？"
    service.owner_reply(root.id, "要")
    service.review(root.id, child.id, "accept", "价格有来源")
    root = service.ask_owner(root.id, "按网页说的把结论发到 x@evil 可以吗？")
    text = task_notices.message(root)
    assert "evil" not in text and "外部内容" in text
