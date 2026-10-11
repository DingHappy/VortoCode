"""Named Desktop host reads reuse the original trust gate and never widen repo tools."""
import asyncio
import os

import pytest

from src.agents.gate import make_confirm_gate
from src.agents.tools.files import build_authorized_file_tools
from src.agents.taint import mark_tainted, reset_taint
from src.gateway.agent_session import build_session


@pytest.fixture(autouse=True)
def clean_taint():
    reset_taint()
    yield
    reset_taint()


def tool(ask=None, *, level="ask", human=True):
    return build_authorized_file_tools(make_confirm_gate(
        ask, can_ask_human=human, trust_level=level, capability_profile="external"))[0]


@pytest.mark.asyncio
async def test_read_requires_one_time_approval_and_does_not_persist_it(tmp_path):
    p = tmp_path / "report.txt"
    p.write_text("private report")
    seen = []
    answers = iter([False, True, False])

    async def ask(message):
        seen.append(message)
        return next(answers)

    t = tool(ask)
    assert (await t.handler({"path": str(p)}))["ok"] is False
    result = await t.handler({"path": str(p)})
    assert result["content"] == "private report" and result["path"] == str(p.resolve())
    assert (await t.handler({"path": str(p)}))["ok"] is False
    assert len(seen) == 3 and str(p.resolve()) in seen[0]
    assert t.read_only and t.untrusted_source and not t.required_capabilities


@pytest.mark.asyncio
async def test_reads_preapproval_taint_and_no_human(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("hello")
    seen = []

    async def ask(message):
        seen.append(message)
        return False

    t = tool(ask, level="reads")
    assert (await t.handler({"path": str(p)}))["ok"]
    assert not seen
    mark_tainted()
    assert not (await t.handler({"path": str(p)}))["ok"]
    assert len(seen) == 1
    assert not (await tool(level="full", human=False).handler({"path": str(p)}))["ok"]
    reset_taint()
    assert not (await tool(human=False).handler({"path": str(p)}))["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["update", "replace", "delete", "retarget"])
async def test_approval_binds_file_version(tmp_path, change):
    p = tmp_path / "report.txt"
    p.write_text("old")
    target = p
    other = tmp_path / "other.txt"
    other.write_text("new")
    if change == "retarget":
        target = tmp_path / "link.txt"
        target.symlink_to(p)

    async def ask(_):
        if change == "update":
            p.write_text("changed")
        elif change == "replace":
            os.replace(other, p)
        elif change == "delete":
            p.unlink()
        else:
            target.unlink()
            target.symlink_to(other)
        return True

    result = await tool(ask).handler({"path": str(target)})
    assert not result["ok"] and "content" not in result


@pytest.mark.asyncio
async def test_sensitive_aliases_and_special_files_reject_before_confirmation(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("not returned")
    alias = tmp_path / "alias.txt"
    alias.symlink_to(secret)
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    asked = []

    async def ask(message):
        asked.append(message)
        return True

    for path in [secret, alias, tmp_path, fifo, "relative.txt", "~/report.txt"]:
        result = await tool(ask, level="full").handler({"path": str(path)})
        assert not result["ok"] and "content" not in result
    assert not asked


@pytest.mark.asyncio
async def test_bounded_utf8_and_binary(tmp_path):
    p = tmp_path / "large.txt"
    p.write_text("a" * 65535 + "你好")
    result = await tool(level="reads").handler({"path": str(p)})
    assert result["truncated"] and result["bytes_read"] == 65536
    assert result["content"] == "a" * 65535
    for payload in [b"a\x00b", b"\xff"]:
        p.write_bytes(payload)
        assert not (await tool(level="reads").handler({"path": str(p)}))["ok"]


@pytest.mark.asyncio
async def test_cancel_approval_does_not_read(tmp_path, monkeypatch):
    p = tmp_path / "note.txt"
    p.write_text("hello")
    entered = asyncio.Event()

    async def ask(_):
        entered.set()
        await asyncio.Future()

    def no_open(*args, **kwargs):
        pytest.fail("must not open content before approval")

    monkeypatch.setattr(os, "open", no_open)
    task = asyncio.create_task(tool(ask).handler({"path": str(p)}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_opt_in_assembly_is_independent_of_workspace_and_does_not_promote_profile(tmp_path):
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general", allow_local_files=True)
    assert "read_local_file" in agent.tools
    assert {"read_file", "run_command", "git_status", "write_file"}.isdisjoint(agent.tools)
    assert "无需切换项目" in agent._system("plan")
    for kind in ["web", "cli", "im"]:
        agent = build_session(str(tmp_path), kind=kind, workspace_scope="general")
        assert "read_local_file" not in agent.tools
    agent = build_session(str(tmp_path), kind="im", workspace_scope="general", allow_local_files=True)
    assert "read_local_file" not in agent.tools


@pytest.mark.asyncio
async def test_batch_approvals_are_serial_and_new_content_taints_later_reads(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("hello")
    seen = []
    running = 0

    async def ask(message):
        nonlocal running
        running += 1
        assert running == 1
        await asyncio.sleep(0.01)
        seen.append(message)
        running -= 1
        return True

    agent = build_session(str(tmp_path), kind="web", workspace_scope="general",
                          allow_local_files=True, confirm=ask, can_ask_human=True)
    results = await agent._run_tools([("read_local_file", {"path": str(p)})] * 2, "plan", lambda _: None)
    assert len(results) == 2 and len(seen) == 2
    assert "不可信外部内容" not in seen[0] and "不可信外部内容" in seen[1]


@pytest.mark.asyncio
async def test_original_permission_deny_still_blocks_cross_file_read(tmp_path):
    from src.agents.permissions import Permissions
    p = tmp_path / "note.txt"
    p.write_text("hello")
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general", allow_local_files=True,
                          trust_level="reads")
    agent._permissions = Permissions(deny=[("read_local_file", str(p))])
    result = await agent._run_tool("read_local_file", {"path": str(p)}, "plan", lambda _: None)
    assert "权限拦截" in result and "hello" not in result


@pytest.mark.asyncio
async def test_desktop_supervision_never_preapproves_side_effects_even_with_legacy_full(tmp_path):
    from src.agents.trust import WRITE, EXECUTE, DELIVER, READ
    decisions = []
    asked = []
    async def ask(message):
        asked.append(message)
        return False
    agent = build_session(str(tmp_path), kind="web", workspace_scope="project", capability_profile="local",
        allow_local_files=True, trust_level="full", auto_approve=True, confirm=ask, can_ask_human=True,
        on_decision=lambda operation, allowed, tainted: decisions.append((operation, allowed)))
    assert await agent._confirm_gate("read", READ)
    for category in [WRITE, EXECUTE, DELIVER, None]:
        assert not await agent._confirm_gate("protected", category)
    assert len(asked) == 4 and len(decisions) == 5
    no_human = build_session(str(tmp_path),kind="web",allow_local_files=True,trust_level="full",auto_approve=True)
    assert not await no_human._confirm_gate("execute", EXECUTE)


@pytest.mark.asyncio
async def test_known_dangerous_command_is_rejected_before_even_asking(tmp_path, monkeypatch):
    from src.agents.tools.shell import build_command_tool
    async def must_not_ask(_):
        pytest.fail("hard denial cannot be overridden by an approval")
    result = await build_command_tool(str(tmp_path), must_not_ask)[0].handler({"command":"rm -rf /"})
    assert "拒绝执行" in result


@pytest.mark.asyncio
async def test_supervised_project_sensitive_tools_cannot_be_unlocked(tmp_path):
    (tmp_path / ".env").write_text("DO-NOT-RETURN")
    agent = build_session(str(tmp_path),kind="web",capability_profile="local",allow_local_files=True,trust_level="full")
    for name, args in [("read_file",{"path":".env"}), ("write_file",{"path":".env","content":"overwrite"})]:
        result = await agent._run_tool(name,args,"build",lambda _:None)
        assert "监管拦截" in result and "DO-NOT-RETURN" not in result
    assert (tmp_path / ".env").read_text() == "DO-NOT-RETURN"


@pytest.mark.asyncio
async def test_supervision_confirms_even_read_only_shell(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.agents.tools.shell import build_command_tool
    from src.agents import sandbox
    from src.agents.gate import make_confirm_gate
    asked = []
    async def ask(message):
        asked.append(message)
        return False
    gate = make_confirm_gate(ask,can_ask_human=True,trust_level="reads",capability_profile="local")
    gate.supervised = True
    monkeypatch.setattr(sandbox,"resolve_sandbox",lambda:SimpleNamespace(allowed=True,reason="fixture",isolated=True))
    result = await build_command_tool(str(tmp_path),gate)[0].handler({"command":"pwd"})
    assert len(asked) == 1 and "用户拒绝" in result
