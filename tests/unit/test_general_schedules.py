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
    # 只读的本机状态只给不联网的作业（与个人资料同一条边界）
    assert ({"today_activity", "schedule_list"} <= seen["tools"]) is (not allow_web)
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


async def test_status_tools_read_local_ledgers(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from src.gateway.session_store import save_session
    from src.scheduling.assistant_watch import build_status_tools

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_session(str(tmp_path), "sid-a", [
        {"role": "user", "text": "宁波天气", "rid": "r1", "at": now},
        {"role": "assistant", "text": "晴", "rid": "r1", "at": now,
         "digest": {"title": "查询宁波天气", "outcome": "晴天", "source": "model"}}], [{"role": "user", "content": "x"}], None)
    tools = {tool.name: tool for tool in build_status_tools(str(tmp_path))}
    assert "查询宁波天气：晴天" in await tools["today_activity"].handler({})
    assert all(tool.read_only for tool in tools.values())


def test_assistant_prompt_explains_what_schedules_can_reach(tmp_path, monkeypatch):
    from src.gateway.agent_session import build_session

    monkeypatch.setenv("VORTOCODE_CRON", "1")
    system = build_session(str(tmp_path), kind="web", workspace_scope="general")._system("plan")
    assert "【定时事项】" in system and "不能**读文件、跑命令、查 Git" in system


@pytest.mark.parametrize("scheduler", ["0", "1"])
def test_interactive_general_has_readonly_continuity_even_without_scheduler(tmp_path, monkeypatch, scheduler):
    from src.gateway.agent_session import build_session
    monkeypatch.setenv("VORTOCODE_CRON", scheduler)
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general")
    assert {"today_activity", "schedule_list", "schedule_history"} <= set(agent.tools)
    assert all(agent.tools[name].read_only for name in ("today_activity", "schedule_list", "schedule_history"))
    assert ("cron_add" in agent.tools) is (scheduler == "1")
    assert not {"read_file", "run_command", "git_status"} & set(agent.tools)


async def test_schedule_tools_report_disabled_scheduler_and_exact_failure(tmp_path, monkeypatch):
    from src.scheduling.assistant_watch import build_status_tools
    from src.scheduling.cron import record_outcome
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "cron.yaml").write_text('jobs:\n  - name: brief\n    schedule: "at 09:00"\n    prompt: "简报"\n')
    monkeypatch.setenv("VORTOCODE_CRON", "0")
    tools = {tool.name: tool for tool in build_status_tools(str(tmp_path))}
    assert "不会自动执行" in await tools["schedule_list"].handler({})
    assert "不能据此判断从未执行" in await tools["schedule_history"].handler({"name": "brief"})
    record_outcome(str(tmp_path), "brief", ok=False, summary="失败", error="连接超时")
    result = await tools["schedule_history"].handler({"name": "brief"})
    assert '"status": "failed"' in result and "连接超时" in result
    assert "没有这个定时作业" in await tools["schedule_history"].handler({"name": "../other"})


def test_continuity_results_mark_external_contents_untrusted(tmp_path, monkeypatch):
    from src.scheduling.assistant_watch import build_status_tools
    from src.workflow.tasks.assistant_dispatch import build_dispatch_tools
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(tmp_path / "registry.json"))
    (tmp_path / "registry.json").write_text('{"projects":[{"name":"demo","repo_root":"/tmp/demo","port":65534}]}')
    tools = {tool.name: tool for tool in build_status_tools(str(tmp_path)) + build_dispatch_tools(str(tmp_path))}
    assert all(tools[name].untrusted_source for name in ("today_activity", "schedule_list", "schedule_history", "list_dispatches"))


def test_general_reporting_stays_evidence_based_when_concise(tmp_path):
    from src.gateway.agent_session import build_session
    system = build_session(str(tmp_path), kind="web", workspace_scope="general")._system("build")
    assert "用户要求详细时再展开" in system
    assert "不能省掉确认和核验" in system
    assert "允许调整查询继续检索" in system
    assert "任务台账为依据" in system
