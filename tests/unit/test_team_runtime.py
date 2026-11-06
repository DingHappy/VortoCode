"""任务上下文的执行（ADR-014 S1b/S1c）：成员按角色装配、结果写回；协调人工具受领域服务约束；调度只在有新情况时叫醒协调人。"""
import asyncio

import pytest

from src.agents import taint
from src.agents import team_coordinator, team_members
from src.scheduling.team_runner import TeamRunner
from src.workflow.tasks.tasks import TaskRunner
from src.workflow.tasks.team import STALL_WAKES, TeamService, load_history

MEMBERS = [{"name": "研究员", "role": "web-researcher"}, {"name": "审查员", "role": "reviewer"}]


@pytest.fixture(autouse=True)
def _clean_taint():
    taint.reset_taint()
    yield
    taint.reset_taint()


def _root(tmp_path, **kwargs):
    service = TeamService(str(tmp_path))
    return service, service.create("sid-owner", objective="比较三家向量数据库", acceptance=["列出价格", "给出推荐"],
                                   members=MEMBERS, **kwargs)


class FakeMember:
    """记录装配参数；``run_turn`` 按脚本返回，可以模拟读了网页。"""
    built = []

    def __init__(self, tools, *, reply="A 10 元（https://a.example）", reads_web=False, delay=0.0, **kwargs):
        self.tools, self.kwargs, self.reply, self.reads_web, self.delay = tools, kwargs, reply, reads_web, delay
        FakeMember.built.append(self)

    async def run_turn(self, prompt, mode="plan"):
        self.prompt, self.mode = prompt, mode
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.reads_web:
            taint.mark_tainted()
        return self.reply

    def set_model(self, model):
        self.model = model


def _factory(**behaviour):
    def make(tools, **kwargs):
        return FakeMember(tools, **behaviour, **kwargs)
    return make


async def test_researcher_gets_only_web_read_tools_and_its_web_reading_taints_the_result(tmp_path):
    service, root = _root(tmp_path)
    child = service.assign(root.id, "研究员", "查 A 的价格", "价格和来源")
    done = await asyncio.create_task(team_members.run_member(   # 后台池里每个成员是独立任务，上下文互不串
        str(tmp_path), root.id, child.id, llm=object(), agent_factory=_factory(reads_web=True)))
    member = FakeMember.built[-1]
    assert sorted(tool.name for tool in member.tools) == ["web_fetch", "web_search"]
    assert member.mode == "plan" and "要交回：价格和来源" in member.prompt
    assert done.status == "done" and done.collaboration["tainted"] is True
    assert not taint.is_tainted()                     # 成员的污点留在子任务上，不串到调用方


async def test_reviewer_has_no_tools_and_empty_or_slow_answers_fail(tmp_path):
    service, root = _root(tmp_path)
    child = service.assign(root.id, "审查员", "审这份材料", "问题清单")
    failed = await team_members.run_member(str(tmp_path), root.id, child.id, llm=object(), agent_factory=_factory(reply=""))
    assert FakeMember.built[-1].tools == [] and failed.status == "failed" and "没有交回" in failed.error
    slow = service.assign(root.id, "审查员", "再审一次", "问题清单")
    task = service.get(root.id)
    task.team["children"]  # 根仍在进行中
    child = service.collaboration(task).get(slow.id)
    child.collaboration["dispatch"]["timeout_seconds"] = 0.01
    service.ledger.save(child)
    timed_out = await team_members.run_member(str(tmp_path), root.id, slow.id, llm=object(),
                                              agent_factory=_factory(delay=1))
    assert timed_out.status == "failed" and "时限" in timed_out.error


def _tool(tools, name):
    return next(tool for tool in tools if tool.name == name)


async def test_coordinator_tools_go_through_the_domain_rules(tmp_path):
    service, root = _root(tmp_path)
    kicked = []
    tools = team_coordinator.build_tools(str(tmp_path), root.id, kick=kicked.append)
    text = await _tool(tools, "team_assign").handler({"member": "研究员", "instruction": "查价格", "expected": "价格表"})
    assert "已派给 研究员" in text and kicked == [root.id]
    child_id = service.get(root.id).team["children"][0]
    work = service.collaboration(service.get(root.id))
    work.start(child_id, 1)
    work.finish(child_id, 1, "A 10 元", tainted=True)
    assert not taint.is_tainted()
    await _tool(tools, "team_result").handler({"task_id": child_id})
    assert taint.is_tainted()                          # 读到外部内容：本回合成为污点
    review = await _tool(tools, "team_cross_review").handler({"task_id": child_id, "reviewer": "审查员"})
    reviewer_task = service.collaboration(service.get(root.id)).get(service.get(root.id).team["children"][1])
    assert "已请 审查员" in review and reviewer_task.collaboration["tainted"] is True
    assert "A 10 元" in reviewer_task.prompt
    refused = await _tool(tools, "team_assign").handler({"member": "开发", "instruction": "再查", "expected": "x"})
    assert refused.startswith("未执行：") and "没有名为 开发 的成员" in refused
    early = await _tool(tools, "team_submit").handler({"summary": "推荐 A", "evidence": [{"criterion": 0, "evidence": "价格表"}]})
    assert early.startswith("未执行：") and "还没有证据" in early
    foreign = await _tool(tools, "team_result").handler({"task_id": "task-elsewhere"})
    assert "不属于本任务上下文" in foreign


async def test_coordinator_turn_saves_its_own_history_and_reads_the_owner_inbox(tmp_path):
    service, root = _root(tmp_path)
    service.ask_owner(root.id, "预算多少？")
    service.owner_reply(root.id, "每月 500 元以内")
    seen = {}

    class Coordinator(FakeMember):
        async def run_turn(self, prompt, mode="plan"):
            seen["prompt"], seen["mode"] = prompt, mode
            await _tool(self.tools, "team_assign").handler({"member": "研究员", "instruction": "查价格", "expected": "价格表"})
            self.history = [{"role": "user", "content": prompt}, {"role": "assistant", "content": "已派研究员"}]
            return "已派研究员"

    error = await team_coordinator.coordinator_turn(str(tmp_path), root.id, ["任务刚开始"], kick=lambda _r: None,
                                                    llm=object(), agent_factory=lambda tools, **kw: Coordinator(tools, **kw))
    assert error is None and seen["mode"] == "build"
    assert "任务刚开始" in seen["prompt"] and "每月 500 元以内" in seen["prompt"]
    root = service.get(root.id)
    assert root.team["used"]["wakes"] == 1 and root.team["wake"]["open"] is False and root.team["wake"]["idle"] == 0
    assert load_history(str(tmp_path), root.id)[-1]["content"] == "已派研究员"
    assert root.team["inbox"] == []


async def test_a_coordinator_error_is_recorded_and_repeats_stop_the_task(tmp_path):
    service, root = _root(tmp_path)

    class Broken(FakeMember):
        async def run_turn(self, prompt, mode="plan"):
            raise RuntimeError("模型服务不可用")

    for _ in range(3):
        error = await team_coordinator.coordinator_turn(str(tmp_path), root.id, ["x"], kick=lambda _r: None,
                                                        llm=object(), agent_factory=lambda t, **kw: Broken(t, **kw))
    assert "模型服务不可用" in error
    assert service.get(root.id).status == "waiting"


async def test_runner_wakes_the_coordinator_only_when_something_new_happens(tmp_path):
    service, root = _root(tmp_path)
    calls = []

    async def coordinator(repo_root, root_id, events, *, kick):
        calls.append(list(events))
        live = TeamService(repo_root)
        live.begin_wake(root_id)
        if len(calls) == 1:
            live.assign(root_id, "研究员", "查价格", "价格表")
            kick(root_id)
        else:
            child_id = live.get(root_id).team["children"][0]
            live.review(root_id, child_id, "accept", "有来源")
            live.submit(root_id, "推荐 A", [{"criterion": 0, "evidence": "价格表"}, {"criterion": 1, "evidence": "A 最便宜"}])
        live.end_wake(root_id)

    async def member(repo_root, root_id, child_id):
        work = TeamService(repo_root).collaboration(TeamService(repo_root).get(root_id))
        work.start(child_id, 1)
        return work.finish(child_id, 1, "A 10 元")

    async def idle(task, progress):
        return ""
    pool = TaskRunner(str(tmp_path), idle, max_concurrent=2)
    runner = TeamRunner(str(tmp_path), pool, member=member, coordinator=coordinator)
    runner.wake(root.id, "任务刚开始")
    for _ in range(50):
        await asyncio.sleep(0.01)
        if service.get(root.id).status == "review":
            break
    assert service.get(root.id).status == "review"
    assert calls[0] == ["任务刚开始"] and "交回了" in calls[1][0] and len(calls) == 2


async def test_restart_reruns_a_cut_coordinator_turn_and_stop_cancels_members(tmp_path):
    service, root = _root(tmp_path)
    service.begin_wake(root.id)                         # 回合进行中被重启打断
    events = []

    async def coordinator(repo_root, root_id, got, *, kick):
        events.append(list(got))

    async def member(repo_root, root_id, child_id):
        await asyncio.sleep(10)

    async def idle(task, progress):
        return ""
    pool = TaskRunner(str(tmp_path), idle, max_concurrent=2)
    runner = TeamRunner(str(tmp_path), pool, member=member, coordinator=coordinator)
    await runner.recover()
    await asyncio.sleep(0.05)
    assert events and "被重启打断" in events[0][0]
    service.end_wake(root.id)
    child = service.assign(root.id, "研究员", "查价格", "价格表")
    runner.kick(root.id)
    await asyncio.sleep(0.05)
    assert pool.is_active(child.id)
    stopped = runner.stop(root.id)
    await asyncio.sleep(0.05)
    assert stopped.status == "cancelled" and not pool.is_active(child.id)
    assert service.collaboration(stopped).get(child.id).status == "cancelled"


async def test_a_coordinator_that_stops_without_a_next_step_is_nudged_until_it_waits_for_the_owner(tmp_path):
    # 真机 2026-11-04：协调人读完审查结果就结束回合，没人在跑、没等主人，小组永远停在「进行中」
    from src.scheduling.team_runner import NUDGE
    service, root = _root(tmp_path)
    calls = []

    async def coordinator(repo_root, root_id, events, *, kick):
        calls.append(list(events))
        live = TeamService(repo_root)
        live.begin_wake(root_id)
        live.end_wake(root_id)                          # 什么都没做

    async def idle(task, progress):
        return ""
    runner = TeamRunner(str(tmp_path), TaskRunner(str(tmp_path), idle), coordinator=coordinator)
    runner.wake(root.id, "任务刚开始")
    for _ in range(50):
        await asyncio.sleep(0.01)
        if service.get(root.id).status == "waiting":
            break
    final = service.get(root.id)
    assert final.status == "waiting" and "没有新进展" in final.team["waiting_reason"]
    assert calls[0] == ["任务刚开始"] and calls[1] == [NUDGE] and len(calls) == 3


async def test_reviewers_cannot_be_told_to_read_other_subtasks_and_cross_review_can_be_focused(tmp_path):
    # 真机 2026-11-04：协调人让审查员「用 team_result 读 task-…」，审查员没有工具，只能空审
    service, root = _root(tmp_path)
    tools = team_coordinator.build_tools(str(tmp_path), root.id, kick=lambda _r: None)
    await _tool(tools, "team_assign").handler({"member": "研究员", "instruction": "查价格", "expected": "价格表"})
    child_id = service.get(root.id).team["children"][0]
    work = service.collaboration(service.get(root.id))
    work.start(child_id, 1)
    work.finish(child_id, 1, "A 10 元，B 20 元")
    refused = await _tool(tools, "team_assign").handler(
        {"member": "审查员", "instruction": f"请读 {child_id} 的结果做终审", "expected": "问题清单"})
    assert refused.startswith("未执行：") and "team_cross_review" in refused
    assert len(service.get(root.id).team["children"]) == 1
    await _tool(tools, "team_cross_review").handler({"task_id": child_id, "reviewer": "审查员", "focus": "只核 B 的价格"})
    reviewer = service.collaboration(service.get(root.id)).get(service.get(root.id).team["children"][1])
    assert "【这次只审这些】只核 B 的价格" in reviewer.prompt and "A 10 元，B 20 元" in reviewer.prompt


def test_a_rework_round_carries_the_previous_result_and_the_rework_note(tmp_path):
    # 真机 2026-11-04：返工时成员只拿到最初的要求，每轮从头重查
    service, root = _root(tmp_path)
    child = service.assign(root.id, "研究员", "查三家的价格", "价格表")
    work = service.collaboration(service.get(root.id))
    work.start(child.id, 1)
    work.finish(child.id, 1, "A 10 元，B 20 元")
    service.review(root.id, child.id, "rework", "缺 C 的价格")
    second = service.collaboration(service.get(root.id)).get(child.id)
    prompt = team_members.member_prompt(second)
    assert prompt.startswith("查三家的价格") and "第 2 轮" in prompt
    assert "【你上一轮交回的】\nA 10 元，B 20 元" in prompt and "【这次要改的】\n缺 C 的价格" in prompt
    work = service.collaboration(service.get(root.id))
    work.start(child.id, 2)
    work.finish(child.id, 2, "", error="超过单轮时限")
    service.review(root.id, child.id, "rework", "再试一次")
    third = team_members.member_prompt(service.collaboration(service.get(root.id)).get(child.id))
    assert "【上一轮没有交回内容】" in third and "A 10 元" in third and "再试一次" in third
    assert "【最近可用的保存稿 · 第 1 轮】" in third and "超过单轮时限" in third
    fresh = service.assign(root.id, "审查员", "审一下", "清单")
    assert team_members.member_prompt(fresh) == "审一下\n\n要交回：清单"          # 第一轮不加东西


def test_rework_preserves_the_saved_result_beyond_the_old_message_limit(tmp_path):
    service, root = _root(tmp_path)
    child = service.assign(root.id, "研究员", "整理结果", "正文")
    work = service.collaboration(service.get(root.id))
    work.start(child.id, 1)
    draft = "前半段" * 1500 + "尾部事实需要保留"
    work.finish(child.id, 1, draft, tainted=True)
    service.review(root.id, child.id, "rework", "只改措辞")
    restored = service.collaboration(service.get(root.id)).get(child.id)
    prompt = team_members.member_prompt(restored)
    assert draft in prompt and "尾部事实需要保留" in prompt
    assert restored.collaboration["tainted"] is True
    assert restored.result == "" and restored.status == "queued"


def test_clipped_and_legacy_drafts_are_not_presented_as_recovered_full_text(tmp_path):
    service, root = _root(tmp_path)
    child = service.assign(root.id, "研究员", "整理结果", "正文")
    work = service.collaboration(service.get(root.id))
    work.start(child.id, 1)
    work.finish(child.id, 1, "稿" * 9000)
    service.review(root.id, child.id, "rework", "核对缺口")
    restored = service.collaboration(service.get(root.id)).get(child.id)
    prompt = team_members.member_prompt(restored)
    assert "保存稿完整性待核对" in prompt and "不要编造" in prompt
    assert "稿" * 9000 not in prompt
    for message in restored.collaboration["messages"]:
        message.pop("truncated", None)
    assert "保存稿完整性待核对" in team_members.member_prompt(restored)


def test_missing_or_future_drafts_do_not_create_context_that_never_existed(tmp_path):
    service, root = _root(tmp_path)
    child = service.assign(root.id, "研究员", "查资料", "正文")
    work = service.collaboration(service.get(root.id))
    work.start(child.id, 1)
    work.finish(child.id, 1, "", error="未取得正文")
    service.review(root.id, child.id, "rework", "明确缺口")
    restored = service.collaboration(service.get(root.id)).get(child.id)
    restored.collaboration["messages"].append({"kind": "submitted", "round": 3, "body": "未来稿件"})
    prompt = team_members.member_prompt(restored)
    assert "未来稿件" not in prompt and "最近可用的保存稿 ·" not in prompt
    assert "明确缺口" in prompt


def test_three_member_failures_in_a_row_hand_the_team_back_to_the_owner(tmp_path):
    # 协调人每次都重新派活，按「醒来有没有新动作」判不出空转
    service, root = _root(tmp_path)
    outcomes = []
    for number in range(STALL_WAKES):
        child = service.assign(root.id, "研究员", f"查第 {number} 次", "价格")
        work = service.collaboration(service.get(root.id))
        work.start(child.id, 1)
        if number == 0:
            done = work.finish(child.id, 1, "A 10 元")
            outcomes.append(service.note_member_result(root.id, done).status)
            continue
        failed = work.finish(child.id, 1, "", error="成员没有交回内容")
        outcomes.append(service.note_member_result(root.id, failed).status)
    assert outcomes == ["active", "active", "active"]                    # 中间有一次交回：重新计数
    for number in range(STALL_WAKES - 2):
        child = service.assign(root.id, "研究员", f"再查 {number}", "价格")
        work = service.collaboration(service.get(root.id))
        work.start(child.id, 1)
        final = service.note_member_result(root.id, work.finish(child.id, 1, "", error="超过单轮时限 600 秒"))
    assert final.status == "waiting" and "成员连续 3 次没有交回结果" in final.team["waiting_reason"]
    assert "超过单轮时限" in final.team["waiting_reason"]


async def test_the_coordinators_submit_ask_and_stall_notify_the_owner(tmp_path, monkeypatch):
    # 真机 2026-11-04：协调人自己 new 的 TeamService 没接通知钩子，小组交付了主人收不到任何通知
    seen = []
    monkeypatch.setattr("src.scheduling.task_notices.notify_transition",
                        lambda root, task: seen.append((task.id, task.status)))
    service, root = _root(tmp_path)
    tools = team_coordinator.build_tools(str(tmp_path), root.id, kick=lambda _r: None)
    await _tool(tools, "team_assign").handler({"member": "研究员", "instruction": "查价格", "expected": "价格表"})
    child_id = service.get(root.id).team["children"][0]
    work = service.collaboration(service.get(root.id))
    work.start(child_id, 1)
    work.finish(child_id, 1, "A 10 元")
    await _tool(tools, "team_ask_owner").handler({"question": "要比较几家？"})
    assert (root.id, "waiting") in seen
    service.owner_reply(root.id, "三家")
    await _tool(tools, "team_review").handler({"task_id": child_id, "verdict": "accept", "note": "有来源"})
    submitted = await _tool(tools, "team_submit").handler({"summary": "推荐 A", "evidence": [
        {"criterion": 0, "evidence": "价格表"}, {"criterion": 1, "evidence": "A 最便宜"}]})
    assert "已交主人验收" in submitted and (root.id, "review") in seen
