import pytest

from src.gateway.agent_session import build_session


def test_general_session_has_no_repository_or_host_tools(tmp_path):
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general")
    names = set(agent.tools)
    assert {"web_fetch", "web_search", "request_workspace", "publish_artifact"} <= names
    assert names.isdisjoint({
        "read_file", "grep", "git_status", "save_memory", "use_skill",
        "run_command", "dev_isolated", "dev_parallel", "open_pr",
    })
    assert agent._env_context is False
    system = agent._system("plan")
    assert "General 无目录会话" in system
    assert "不要把最近项目或 HOME 当作隐式工作区" in system


@pytest.mark.asyncio
async def test_general_workspace_request_is_structured(tmp_path):
    seen = []
    agent = build_session(
        str(tmp_path), kind="web", workspace_scope="general",
        on_workspace_required=lambda scope, reason, task: seen.append((scope, reason, task)),
    )
    result = await agent.tools["request_workspace"].handler({
        "scope": "scratch",
        "reason": "需要运行一个 Python 示例",
        "task": "创建最小示例并运行测试",
    })
    assert seen == [("scratch", "需要运行一个 Python 示例", "创建最小示例并运行测试")]
    assert "等待用户审阅并切换" in result


def test_scratch_keeps_tools_inside_an_explicit_workspace(tmp_path):
    agent = build_session(
        str(tmp_path), kind="web", workspace_scope="scratch", capability_profile="local",
    )
    assert "read_file" in agent.tools
    assert "run_command" in agent.tools
    assert "request_workspace" in agent.tools
    assert "Scratch 隔离临时工作区" in agent._system("plan")
