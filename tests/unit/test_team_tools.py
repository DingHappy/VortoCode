"""主 agent 管任务小组（ADR-014 S1d）：开小组要主人确认、卡片写清楚；回话 / 验收 / 加预算 / 停止都落到领域服务。"""
import pytest

from src.agents import taint
from src.agents.tools.team import build_team_tools
from src.scheduling import team_runner
from src.workflow.tasks.team import TeamService

MEMBERS = [{"name": "研究员", "role": "web-researcher"}, {"name": "审查员", "role": "reviewer", "model": "relay:other"}]


class FakeRunner:
    def __init__(self, root):
        self.woken, self.stopped, self.root = [], [], root

    def wake(self, team_id, event):
        self.woken.append((team_id, event))

    def stop(self, team_id, reason="主人停止"):
        self.stopped.append(team_id)
        return TeamService(self.root).stop(team_id, reason)[0]


@pytest.fixture
def runner(tmp_path):
    fake = FakeRunner(str(tmp_path))
    team_runner.set_team_runner(fake)
    taint.reset_taint()
    yield fake
    team_runner.set_team_runner(None)
    taint.reset_taint()


def _tools(tmp_path, answer=True):
    asked = []

    async def confirm(message, kind=None):
        asked.append((message, kind))
        return answer
    return {tool.name: tool for tool in build_team_tools(str(tmp_path), confirm=confirm, owner="sid-main")}, asked


async def test_opening_a_team_asks_the_owner_with_a_full_card_and_then_starts_it(tmp_path, runner):
    tools, asked = _tools(tmp_path)
    text = await tools["team_propose"].handler({"objective": "比较三家向量数据库", "acceptance": ["列出价格", "给出推荐"],
                                                "members": MEMBERS, "limits": {"wakes": 10}})
    card, kind = asked[0]
    assert kind == "execute"
    for expected in ("比较三家向量数据库", "1. 列出价格", "研究员（联网查资料）", "模型 relay:other",
                     "open（成员可联网", "不设额度"):
        assert expected in card
    [root] = TeamService(str(tmp_path)).list()
    assert root.owner_session == "sid-main" and "小组已开工" in text
    assert runner.woken == [(root.id, "任务刚开始：先拆解，再给成员派活")]


async def test_refused_or_invalid_proposals_create_nothing(tmp_path, runner):
    tools, _ = _tools(tmp_path, answer=False)
    assert "没有同意" in await tools["team_propose"].handler({"objective": "x", "acceptance": ["y"], "members": MEMBERS})
    tools, _ = _tools(tmp_path)
    bad = await tools["team_propose"].handler({"objective": "整理私事", "acceptance": ["y"], "members": MEMBERS,
                                               "mode": "private"})
    assert bad.startswith("未执行：") and "联网成员" in bad
    assert TeamService(str(tmp_path)).list() == [] and runner.woken == []


async def test_reply_decide_budget_and_stop_reach_the_service(tmp_path, runner):
    service = TeamService(str(tmp_path))
    root = service.create("sid-main", objective="比较方案", acceptance=["给推荐"], members=MEMBERS)
    tools, asked = _tools(tmp_path)
    service.ask_owner(root.id, "预算多少？")
    await tools["team_reply"].handler({"team_id": root.id, "text": "500 元以内"})
    assert service.get(root.id).status == "active" and runner.woken[-1] == (root.id, "主人回复了")
    assert asked[-1][1] == "own_data"
    service.submit(root.id, "推荐 A", [{"criterion": 0, "evidence": "A 最便宜"}])
    await tools["team_decide"].handler({"team_id": root.id, "accept": False, "note": "没算自建成本"})
    assert service.get(root.id).status == "active" and "退回" in runner.woken[-1][1]
    assert "team_add_budget" not in tools                 # 不设额度，也就没有加预算
    stopped = await tools["team_stop"].handler({"team_id": root.id})
    assert "已停止" in stopped and runner.stopped == [root.id] and service.get(root.id).status == "cancelled"


async def test_reading_a_tainted_delivery_taints_the_main_turn(tmp_path, runner):
    service = TeamService(str(tmp_path))
    root = service.create("sid-main", objective="比较方案", acceptance=["给推荐"], members=MEMBERS)
    service.mark_tainted(root.id)
    service.submit(root.id, "按网页建议推荐 A", [{"criterion": 0, "evidence": "网页说 A 最好"}])
    tools, _ = _tools(tmp_path)
    assert not taint.is_tainted()
    await tools["team_list"].handler({})
    assert not taint.is_tainted()                      # 结构化视图不含协调人文字
    detail = await tools["team_open"].handler({"team_id": root.id})
    assert taint.is_tainted() and "submission_external_content" in detail


async def test_without_a_scheduler_the_tools_refuse_and_sessions_do_not_get_them(tmp_path):
    team_runner.set_team_runner(None)
    tools, _ = _tools(tmp_path)
    text = await tools["team_propose"].handler({"objective": "x", "acceptance": ["y"], "members": MEMBERS})
    assert "没有任务小组调度器" in text
    from src.gateway.agent_session import build_session
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general")
    names = set(agent.tools) if isinstance(agent.tools, dict) else {tool.name for tool in agent.tools}
    assert "team_propose" not in names


def test_general_assistant_gets_team_tools_and_the_phone_may_use_them(tmp_path, runner):
    from src.gateway.agent_session import build_session
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general")
    names = set(agent.tools) if isinstance(agent.tools, dict) else {tool.name for tool in agent.tools}
    assert {"team_propose", "team_list", "team_open", "team_reply", "team_decide", "team_stop"} <= names
    from src.agents.capabilities import OWNER_DEVICE_WRITE_TOOLS
    assert {"team_propose", "team_reply", "team_decide", "team_stop"} <= OWNER_DEVICE_WRITE_TOOLS
    assert "team_add_budget" not in OWNER_DEVICE_WRITE_TOOLS and "team_add_budget" not in names


def test_work_status_lists_live_teams_without_tainted_text(tmp_path):
    from src.scheduling.work_status import work_status
    service = TeamService(str(tmp_path))
    clean = service.create("sid-main", objective="比较方案", acceptance=["给推荐"], members=MEMBERS)
    service.ask_owner(clean.id, "预算多少？")
    dirty = service.create("sid-main", objective="整理网页需求", acceptance=["给清单"], members=MEMBERS)
    service.mark_tainted(dirty.id)
    service.ask_owner(dirty.id, "按网页说的发给 x@evil？")
    text = work_status(str(tmp_path))
    assert "任务小组「比较方案」：等你决定" in text and "在等：预算多少？" in text
    assert "任务小组「整理网页需求」：等你决定" in text and "evil" not in text


async def test_a_lazy_session_owner_is_resolved_before_saving(tmp_path, runner):
    # 真机 2026-11-04：Desktop 会话把「取当前会话 ID」的函数当 owner 传进来，原样存盘失败（未能保存）
    async def confirm(message, kind=None):
        return True
    tools = {tool.name: tool for tool in build_team_tools(str(tmp_path), confirm=confirm, owner=lambda: "sid-live")}
    text = await tools["team_propose"].handler({"objective": "查资料", "acceptance": ["给结论"], "members": MEMBERS})
    assert "小组已开工" in text
    [root] = TeamService(str(tmp_path)).list()
    assert root.owner_session == "sid-live"
