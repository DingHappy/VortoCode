"""Private-project reader: actual tool execution and IM turns, all offline."""
import asyncio
import json
import os
import subprocess

import pytest

from src.agents.capabilities import RESEARCH_READ_PROFILE
from src.agents.tool import Tool
from src.gateway.agent_session import build_session
from src.gateway import im_service
from src.im.bridge import IMBridge
from src.im.channel import ChannelEvent
from tests.unit.test_im_bot_isolation import AccountAdapter
from tests.unit.test_im_bridge import ScriptedLLM, _run_turn_to_completion
from tests.unit.test_im_in_gateway import FakeRunner


def reader(root, **kwargs):
    return build_session(str(root), kind="im", session_role="research_read", **kwargs)


def bot(root, *, llm=None, **kwargs):
    return IMBridge(str(root), AccountAdapter(), "owner-1", channel="telegram",
                    conversation="isolated", session_role="research_read",
                    llm=llm or ScriptedLLM("离线结论"), **kwargs)


async def test_research_reads_project_evidence_without_a_confirmation_or_code_change(tmp_path):
    source = tmp_path / "login.py"
    source.write_text("def login():\n    return 'needs-token'\n")
    llm = ScriptedLLM(json.dumps({"tool": "read_file", "args": {"path": "login.py", "start": 1, "end": 2}}),
                      "依据 login.py 第2行，缺少令牌。未运行验证。")
    instance = bot(tmp_path, llm=llm)
    await _run_turn_to_completion(instance, instance.adapter,
                                 ChannelEvent(kind="message", sender_id="owner-1", text="定位登录失败的代码依据"))
    assert llm.n == 2
    assert "needs-token" in str(instance.agent.history)
    assert "login.py" in instance.adapter.texts()[-1]
    assert source.read_text() == "def login():\n    return 'needs-token'\n"
    assert not [row for row in instance.adapter.sent if row[0] == "confirm"]
    assert instance._load_persona() == ""  # The question wasn't consumed as persona onboarding.


@pytest.mark.parametrize("name", ["write_file", "run_command", "web_fetch", "task", "mcp__remote"])
async def test_late_registered_tools_cannot_expand_research_even_in_build_mode(tmp_path, name):
    invoked = []

    async def forbidden(_args):
        invoked.append(name)
        (tmp_path / "changed.txt").write_text("unexpected side effect")
        return "executed"

    agent = reader(tmp_path)
    # A supposedly read-only wrapper is also denied by name at the shared tool boundary.
    tool = Tool(name, "late tool", {}, forbidden, read_only=True)
    agent.add_tools([tool])
    result = await agent._run_tool(name, {}, "build", lambda _message: None)
    assert "只读研究策略" in result
    assert not invoked and not (tmp_path / "changed.txt").exists()


@pytest.mark.parametrize("read_only,outward", [(False, False), (True, True)])
async def test_known_tool_name_cannot_hide_write_or_outbound_metadata(tmp_path, read_only, outward):
    invoked = []

    async def forbidden(_args):
        invoked.append(True)

    agent = reader(tmp_path)
    forged = Tool("read_file", "incompatible adapter", {}, forbidden, read_only=read_only, outward=outward)
    result = await agent._run_bound_tool(forged, {}, "build", lambda _message: None)
    assert "只读研究策略" in result and not invoked


async def test_model_cannot_create_a_file_or_request_a_build_upgrade(tmp_path):
    llm = ScriptedLLM('{"tool":"write_file","args":{"path":"changed.py","content":"bad"}}',
                      '{"tool":"request_build","args":{"reason":"pretend owner approved"}}', "只能提出修改建议。")
    agent = reader(tmp_path, llm=llm, auto_approve=True, can_ask_human=True)
    await agent.run_turn("直接修改并运行", mode="build")
    assert not (tmp_path / "changed.py").exists()
    assert llm.n == 3


@pytest.mark.parametrize("context", [0, 2])
@pytest.mark.parametrize("git_repo", [False, True])
async def test_bulk_reads_do_not_follow_an_outside_project_symlink(tmp_path, context, git_repo):
    root = tmp_path / "project"
    root.mkdir()
    if git_repo:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    outside = tmp_path / "unrelated.py"
    outside.write_text("OUTSIDE_PROJECT_MARKER")
    (root / "alias.py").symlink_to(outside)
    (root / "inside.py").write_text("inside evidence")
    agent = reader(root)
    result = await agent._run_tool("grep", {"pattern": "MARKER", "context": context}, "plan", lambda _m: None)
    assert "OUTSIDE_PROJECT_MARKER" not in result
    listing = await agent._run_tool("list_files", {}, "plan", lambda _m: None)
    assert "alias.py" not in listing and "inside.py" in listing


async def test_direct_reads_reject_sensitive_files_and_parent_or_symlink_escape(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "unrelated.txt").write_text("OUTSIDE_VALUE")
    (root / "alias.txt").symlink_to(tmp_path / "unrelated.txt")
    (root / ".env").write_text("PRIVATE_VALUE")
    state = root / ".vortocode"
    state.mkdir()
    (state / "private.txt").write_text("PRIVATE_VALUE")
    (root / "alias-state.txt").symlink_to(state / "private.txt")
    agent = reader(root)
    for path in ("../unrelated.txt", str(tmp_path / "unrelated.txt"), "alias.txt", ".env",
                 ".vortocode/private.txt", "alias-state.txt"):
        result = await agent._run_tool("read_file", {"path": path}, "build", lambda _m: None)
        assert "OUTSIDE_VALUE" not in result and "PRIVATE_VALUE" not in result


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX named pipes")
async def test_bulk_reads_exclude_a_named_pipe_before_trying_to_open_it(tmp_path):
    os.mkfifo(tmp_path / "pipe.py")
    agent = reader(tmp_path)
    listing = await agent._run_tool("list_files", {}, "plan", lambda _m: None)
    assert "pipe.py" not in listing
    # If enumeration didn't reject it, a normal text open would wait for a writer.
    result = await agent._run_tool("grep", {"pattern": "marker"}, "plan", lambda _m: None)
    assert "没有匹配" in result


def test_role_ignores_expansive_ambient_defaults_and_does_not_load_other_context(tmp_path, monkeypatch):
    from src.agents import project, repo_memory
    from src.agents import main_agent

    def forbidden(*_args, **_kwargs):
        pytest.fail("research assembly loaded ambient instructions, skills, or memory")

    monkeypatch.setattr(project, "load_user_instructions", forbidden)
    monkeypatch.setattr(project, "load_project_instructions", forbidden)
    monkeypatch.setattr(repo_memory, "load_repo_memory", forbidden)
    monkeypatch.setattr(main_agent, "skill_catalog", forbidden)
    for key in ("VORTOCODE_MAX_STEPS", "VORTOCODE_BUILD_MAX_STEPS", "VORTOCODE_BUILD_AUTO_CONTINUES"):
        monkeypatch.setenv(key, "1000")
    monkeypatch.setenv("VORTOCODE_COMPACT", "1")
    agent = reader(tmp_path, capability_profile="local", with_dev=True, allow_local_files=True)
    assert agent._capabilities.profile == RESEARCH_READ_PROFILE
    assert agent.max_steps == agent.build_max_steps == 12
    assert agent.build_auto_continues == 0 and not agent.compact
    assert not agent._auto_recall and not agent._env_context
    assert set(agent.tools) == {"read_file", "list_files", "grep"}


@pytest.mark.parametrize("rules", ["[", "[]", "false", "0", "[read_file]", "deny: read_file",
                                   "profile: missing", "deny: [42]", "profiles: []"])
def test_invalid_project_permissions_cannot_silently_remove_restrictions(tmp_path, rules):
    state = tmp_path / ".vortocode"
    state.mkdir()
    (state / "permissions.yaml").write_text(rules)
    with pytest.raises(ValueError, match="权限"):
        reader(tmp_path)


@pytest.mark.parametrize("rules", ["deny: [read_file]", "["])
async def test_new_denies_or_damaged_rules_block_the_next_read(tmp_path, rules):
    (tmp_path / "private.py").write_text("SHOULD_NOT_BE_READ")
    agent = reader(tmp_path)
    state = tmp_path / ".vortocode"
    state.mkdir()
    (state / "permissions.yaml").write_text(rules)
    result = await agent._run_tool("read_file", {"path": "private.py"}, "build", lambda _m: None)
    assert "权限" in result and "SHOULD_NOT_BE_READ" not in result


@pytest.mark.parametrize("scope", ["general", "scratch", "unknown"])
def test_reader_requires_a_project_scope(tmp_path, scope):
    with pytest.raises(ValueError, match="Project"):
        reader(tmp_path, workspace_scope=scope)


@pytest.mark.parametrize("steps", [0, -1, True, "12", 13])
def test_step_contract_cannot_be_expanded(tmp_path, steps):
    with pytest.raises(ValueError, match="步数"):
        reader(tmp_path, max_steps=steps)


def test_switching_a_standard_isolated_bot_to_reader_never_adopts_its_history(tmp_path):
    previous = IMBridge(str(tmp_path), AccountAdapter(), "owner-1", channel="telegram",
                        conversation="isolated", llm=ScriptedLLM("offline"))
    previous.agent.history = [{"role": "user", "content": "standard private history"}]
    previous._save_persona("standard persona")
    previous._persist()
    instance = bot(tmp_path)
    assert instance._sid != previous._sid and not instance.agent.history
    assert instance._load_persona() == ""
    instance.agent.history = [{"role": "user", "content": "research context"}]
    instance.mode = "build"  # A saved or stale mode cannot expand the role on restart.
    instance._persist()
    restored = bot(tmp_path)
    assert restored._sid == instance._sid and restored.agent.history == instance.agent.history
    assert restored.mode == "plan"


@pytest.mark.parametrize("count", [1, 2, 4, 8])
async def test_many_reader_instances_keep_parallel_turns_and_histories_separate(tmp_path, count):
    # This exercises N actual cores/bridge instances, not embedded N-account hosting.
    instances = []
    clients = []
    for index in range(count):
        name = f"evidence-{index}.py"
        (tmp_path / name).write_text(f"OBSERVED_{index}_ONLY")
        client = ScriptedLLM(json.dumps({"tool": "read_file", "args": {"path": name}}),
                             f"RESULT_{index}_ONLY")
        clients.append(client)
        instances.append(IMBridge(str(tmp_path), AccountAdapter(f"bot-{index}"), "owner-1",
                                  channel="telegram", conversation="isolated", session_role="research_read",
                                  llm=client))
    await asyncio.gather(*(instance._run_turn(f"PRIVATE_QUESTION_{index}_ONLY 读取 evidence-{index}.py")
                           for index, instance in enumerate(instances)))
    assert len({instance._sid for instance in instances}) == count
    assert len({id(instance.agent) for instance in instances}) == count
    for index, instance in enumerate(instances):
        history = str(instance.agent.history)
        assert f"OBSERVED_{index}_ONLY" in history and clients[index].n == 2
        assert instance._transcript[-1]["text"] == f"RESULT_{index}_ONLY"
        assert all(f"PRIVATE_QUESTION_{other}_ONLY" not in history for other in range(count) if other != index)


async def test_reader_does_not_read_project_pipeline_metadata(tmp_path, monkeypatch):
    instance = bot(tmp_path)

    def forbidden(*_args):
        pytest.fail("read-only bot consumed unrelated pipeline state")

    for method in ("_pipeline_pointer", "_pipeline_status_line", "_recent_plan"):
        monkeypatch.setattr(instance, method, forbidden)
    await instance._run_turn("给出只读研究结论")
    await instance._handle_command("/status")
    await instance._handle_command("/mode build")
    assert instance.mode == "plan" and not instance._with_dev


@pytest.mark.parametrize("conversation,notification", [("primary", False), ("isolated", True)])
def test_reader_cannot_become_a_primary_or_notification_sink(tmp_path, conversation, notification):
    with pytest.raises(ValueError, match="isolated interactive"):
        IMBridge(str(tmp_path), AccountAdapter(), "owner-1", conversation=conversation,
                 session_role="research_read", notification_only=notification)


def test_reader_does_not_share_project_material_with_an_expanded_allowlist(tmp_path):
    with pytest.raises(ValueError, match="配对主人"):
        bot(tmp_path, allow_from={"owner-1", "other-user"})


def test_embedded_entry_uses_the_same_role_without_stealing_owner_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_IM_POLICY", "research_read")
    monkeypatch.setenv("VORTOCODE_IM_CONVERSATION", "isolated")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    registered = []
    runner = FakeRunner()
    try:
        instance, _ = im_service.start_embedded("telegram", str(tmp_path), adapter=AccountAdapter(),
                                              owner="owner-1", runner=runner, register_worker=registered.append)
        assert instance.agent._capabilities.profile == RESEARCH_READ_PROFILE
        assert not registered and not runner.subs
    finally:
        im_service.stop_embedded()
    assert not registered


@pytest.mark.parametrize("scope", ["general", "scratch", "projet", ""])
def test_host_policy_requires_explicit_project_selection(tmp_path, monkeypatch, scope):
    monkeypatch.setenv("VORTOCODE_IM_POLICY", "research_read")
    monkeypatch.setenv("VORTOCODE_IM_CONVERSATION", "isolated")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", scope)
    with pytest.raises(im_service.IMConfigError, match="explicit"):
        im_service.load_session_role()


@pytest.mark.parametrize("policy,conversation,role", [
    ("reseach_read", "isolated", "interactive"), ("research_read", "primary", "interactive"),
    ("research_read", "isolated", "notify"), ("", "isolated", "interactive"),
])
def test_invalid_host_config_never_falls_back_to_standard(tmp_path, monkeypatch, policy, conversation, role):
    monkeypatch.setenv("VORTOCODE_IM_POLICY", policy)
    monkeypatch.setenv("VORTOCODE_IM_CONVERSATION", conversation)
    monkeypatch.setenv("VORTOCODE_IM_ROLE", role)
    with pytest.raises(im_service.IMConfigError):
        im_service.load_session_role()
