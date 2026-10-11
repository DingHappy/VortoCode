"""常驻助理（通用范围）的定时事项：无人值守会话没有文件/命令，联网要逐个作业授权；个人资料不与出站通道同处。"""
from __future__ import annotations

import pytest

from src.gateway import session
from src.memory.assistant_profile import save_profile


@pytest.fixture
def general(monkeypatch):
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")


def _capture(monkeypatch):
    seen = {}

    async def run_turn(self, prompt, **_kw):
        seen["tools"] = set(self.tools)
        seen["system"] = self._system("build")
        return "ok"
    monkeypatch.setattr("src.agents.main_agent.MainAgent.run_turn", run_turn)
    return seen


@pytest.mark.parametrize("allow_web", [False, True])
async def test_general_unattended_session_tools_and_context(tmp_path, monkeypatch, general, allow_web):
    (tmp_path / ".vortocode").mkdir()
    save_profile(str(tmp_path), name="小V", soul="简短直接", about_you="住在宁波，手机号 138xxxx")
    seen = _capture(monkeypatch)
    assert await session.run_isolated_session(str(tmp_path), "每天早上给我一份简报", allow_web=allow_web) == "ok"
    file_or_host = {"read_file", "write_file", "edit_file", "run_command", "git_status", "save_memory",
                    "cron_add", "send_image", "send_file", "dev_auto", "remember_repo"}
    assert not seen["tools"] & file_or_host                                  # 一个工作区工具都没有
    assert (seen["tools"] >= {"web_fetch", "web_search"}) is allow_web       # 联网只在授权时给
    assert "「小V」" in seen["system"] and "简短直接" in seen["system"]
    # 能联网的作业不带「关于你」与记忆（出站通道 + 个人资料不同处）；不联网的带上
    assert ("住在宁波" in seen["system"]) is (not allow_web)


@pytest.mark.parametrize("scope, heartbeat, refused", [
    ("project", "1", False), ("general", "", False), ("general", "1", True), ("scratch", "", True)])
def test_scheduling_scope_gate(monkeypatch, scope, heartbeat, refused):
    from src.gateway.runtime import scheduling_scope_error

    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", scope)
    monkeypatch.setenv("VORTOCODE_HEARTBEAT", heartbeat)
    assert bool(scheduling_scope_error()) is refused


async def test_command_job_refused_in_general_scope(tmp_path, monkeypatch, general):
    from src.scheduling import cron

    ran, notices = [], []

    async def fake_command(*_a, **_k):
        ran.append(1)
        return 0, "", {}

    async def notify(text, **_kw):
        notices.append(text)
    monkeypatch.setattr(cron, "run_command_job", fake_command)
    job = cron.CronJob(name="clean", schedule=cron.parse_schedule("at 03:00"), command="rm -rf /tmp/x")
    with pytest.raises(PermissionError):
        await cron.run_job(str(tmp_path), job, notify=notify)
    assert ran == [] and notices and "不跑 command 作业" in notices[-1]
