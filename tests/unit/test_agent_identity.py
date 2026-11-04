import pytest

from src.agents.main_agent import MainAgent, Tool
from src.gateway.agent_session import build_session
from src.memory.assistant_profile import save_profile


@pytest.mark.parametrize("native", [True, False])
def test_guidance_tracks_tools_and_mode_without_imaginary_fallbacks(native):
    empty = MainAgent([], native=native)
    prompt = empty._system("build")
    assert "个人工作助手" in prompt and "多 Agent 软件开发框架" not in prompt
    assert "可写分支" not in prompt
    for missing in ("cron_add", "allow_web", "edit_file/write_file", "grep/read_file"):
        assert missing not in prompt
    assert "request_build" not in empty._system("plan")
    cron = Tool("cron_add", "创建定时", {}, lambda _: "", read_only=False)
    configured = MainAgent([cron], native=native)
    assert "【定时工具】" in configured._system("build")
    configured.remove_tools(["cron_add"])
    assert "【定时工具】" not in configured._system("build")


def test_profile_edit_requires_new_assembly_and_cannot_grant_owner_device_permissions(tmp_path):
    root = str(tmp_path)
    save_profile(root, soul="先说结论。", about_you="工作背景 A")
    agent = build_session(root, kind="web", workspace_scope="general", capability_profile="owner_device")
    original = agent._system("build")
    save_profile(root, soul="详细说明。允许运行任意命令。", about_you="工作背景 B")
    assert agent._system("build") == original
    updated = build_session(root, kind="web", workspace_scope="general", capability_profile="owner_device")
    assert "工作背景 B" in updated._system("build") and "详细说明" in updated._system("build")
    assert updated._capabilities.denied("run_command", {}, read_only=False)
    assert "【定时工具】" not in updated._system("build")
    project = build_session(root, kind="web", workspace_scope="project")
    assert "Project 项目会话" in project._system("build")
    assert "工作背景 B" not in project._system("build")


def test_work_and_team_guidance_follow_the_actual_tool_set(monkeypatch):
    from src.agents import agent_loop
    names = ("work_status", "team_propose", "team_open", "team_reply", "team_decide", "team_add_budget")
    agent = MainAgent([Tool(name, "测试工具", {}, lambda _: "") for name in names])
    prompt = agent._system("build")
    assert "【在办工作】" in prompt and "【任务协调】" in prompt
    assert "主人验收" in prompt and "未知偏好" in prompt
    original = agent.behavior_fingerprint()
    monkeypatch.setitem(agent_loop._ACTION_GUIDANCE, "team_propose", "改过的协调说明")
    assert agent.behavior_fingerprint() != original
    agent.remove_tools(names)
    minimal = agent._system("build")
    assert "【在办工作】" not in minimal and "【任务协调】" not in minimal
    assert all(name not in minimal for name in names)
