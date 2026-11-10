"""owner_device 能力档案（ADR-012 / v4 审查 V2）：手机 App 发起的回合走公网入口，只能读与检索、
查资料、改主人自己的事项与记忆；命令执行、写文件、操控电脑与浏览器、派开发任务、对外发送一律拒绝。"""

from src.agents.gate import make_confirm_gate

import pytest

from src.agents.capabilities import (AUTHENTICATED_OUTBOUND, HOST_PROCESS, OWNER_DEVICE_PROFILE,
                                     SENSITIVE_FILES, SessionCapabilities, normalize_profile)
from src.agents.main_agent import MainAgent, Tool


def test_profile_is_known_and_has_no_credential_capabilities():
    assert normalize_profile("owner_device") == OWNER_DEVICE_PROFILE
    caps = SessionCapabilities.for_profile(OWNER_DEVICE_PROFILE)
    assert not caps.allowed & {HOST_PROCESS, AUTHENTICATED_OUTBOUND, SENSITIVE_FILES}
    assert "手机" in caps.system_notice()


def _assemble(tmp_path, scope):
    """用真实装配出来的全套工具逐个问档案：放不放行。"""
    from src.gateway.agent_session import build_session

    agent = build_session(str(tmp_path), kind="web", capability_profile=OWNER_DEVICE_PROFILE,
                          workspace_scope=scope)
    assert agent._capabilities.profile == OWNER_DEVICE_PROFILE
    caps = agent._capabilities
    decisions = {name: caps.denied(name, {}, tool.required_capabilities, external_content=tool.external_content,
                                   read_only=tool.read_only, outward=tool.outward)
                 for name, tool in agent.tools.items()}
    return agent.tools, decisions


@pytest.mark.parametrize("scope", ["general", "project"])
def test_assembled_tools_are_limited_to_reads_research_and_owner_data(tmp_path, scope):
    tools, decisions = _assemble(tmp_path, scope)
    if scope == "general":                      # 常驻助理的范围：手机最常用的写入必须真的在
        assert {"personal_goal_create", "personal_goal_update", "web_search"} <= set(decisions)
    allowed = {name for name, reason in decisions.items() if reason is None}
    for name in ("run_command", "write_file", "edit_file", "open_pr", "send_file", "send_image",
                 "send_document", "desktop_click", "desktop_type", "desktop_screenshot", "dev_auto",
                 "dev_isolated", "task", "task_followup", "task_review", "task_acknowledge", "read_local_file",
                 "screenshot_page", "save_skill", "cron_add"):
        if name in decisions:
            assert name not in allowed, name
    for name in ("read_file", "grep", "web_search", "recall_memory", "personal_goal_create",
                 "personal_goal_update", "save_memory", "propose_task", "propose_codex_message",
                 "propose_codex_interrupt"):
        if name in decisions:
            assert name in allowed, name
    # 放行的写入只有主人自己的事项与记忆
    from src.agents.capabilities import OWNER_DEVICE_WRITE_TOOLS
    assert {name for name in allowed if not tools[name].read_only} <= OWNER_DEVICE_WRITE_TOOLS


@pytest.mark.parametrize("outward", [True, False])
def test_outward_and_unlisted_writes_are_refused(outward):
    caps = SessionCapabilities.for_profile(OWNER_DEVICE_PROFILE)
    reason = caps.denied("anything_new", {}, read_only=False, outward=outward)
    assert reason and ("Desktop" in reason)


def test_sensitive_files_stay_closed(tmp_path):
    caps = SessionCapabilities.for_profile(OWNER_DEVICE_PROFILE, str(tmp_path))
    assert caps.denied("read_file", {"path": ".env"}) is not None
    assert caps.denied("read_file", {"path": "notes.md"}) is None


async def test_refusal_happens_before_the_handler_runs(tmp_path):
    ran = []

    async def handler(_args):
        ran.append(True)
        return "ran"

    class LLM:
        def __init__(self):
            self.replies = ['{"tool":"run_command","args":{"command":"ls"}}', "好。"]

        async def chat(self, messages, **_kwargs):
            return {"content": self.replies.pop(0)}

    agent = MainAgent([Tool("run_command", "跑命令", {}, handler, read_only=False,
                            required_capabilities=(HOST_PROCESS,), operation_kind='write', confirm=make_confirm_gate(auto_approve=True))], llm=LLM())
    agent._capabilities = SessionCapabilities.for_profile(OWNER_DEVICE_PROFILE, str(tmp_path))
    await agent.run_turn("帮我跑一下 ls", mode="build")
    assert ran == []
    assert any("Desktop" in str(m.get("content")) for m in agent.history if m.get("role") == "user")
