"""web 后端地基测试：agent 持久化 + 实时 agent_status 广播（离线）。"""

import pytest


def test_custom_agent_persists_across_reload(tmp_path):
    from src.agents.custom_agent import CustomAgentManager

    p = str(tmp_path / "ca.json")
    m1 = CustomAgentManager(persist_path=p)
    a = m1.create_agent(name="我的Agent", description="d")

    m2 = CustomAgentManager(persist_path=p)          # 模拟重启
    got = m2.get_agent(a.id)
    assert got is not None and got.name == "我的Agent"


def test_custom_agent_delete_persists(tmp_path):
    from src.agents.custom_agent import CustomAgentManager

    p = str(tmp_path / "ca.json")
    m1 = CustomAgentManager(persist_path=p)
    a = m1.create_agent(name="x")
    m1.delete_agent(a.id)

    assert CustomAgentManager(persist_path=p).get_agent(a.id) is None


def test_advanced_agent_persists_without_duplicating_defaults(tmp_path):
    from src.agents.manager import AgentManager

    p = str(tmp_path / "aa.json")
    m1 = AgentManager(persist_path=p)                # 首次：建 5 个默认并落盘
    base = len(m1.list_agents())
    assert base == 5
    a = m1.create_agent(name="自建", role="custom")    # 返回 AgentInstance（id 在 .config.id）

    m2 = AgentManager(persist_path=p)                # 重启：加载（5 默认 + 1 自建），不重复 seed
    assert len(m2.list_agents()) == base + 1
    assert m2.get_agent(a.config.id) is not None


def test_advanced_manager_no_persist_is_backward_compatible():
    from src.agents.manager import AgentManager

    m = AgentManager()                               # 不传 persist_path → 旧行为
    assert m.persist_path is None
    assert len(m.list_agents()) == 5                 # 仍有 5 个默认


@pytest.mark.asyncio
async def test_set_agent_updates_state_and_broadcasts(monkeypatch):
    from src.web.routers import execution

    sent = []

    async def fake_broadcast(msg):
        sent.append(msg)

    monkeypatch.setattr(execution.manager, "broadcast", fake_broadcast)

    await execution._set_agent("developer", "running", "写代码")

    assert execution.state.agents["developer"] == {"status": "running", "current_task": "写代码"}
    assert sent and sent[-1]["type"] == "agent_status"
    assert sent[-1]["data"]["agent_id"] == "developer"
    assert sent[-1]["data"]["status"] == "running"
