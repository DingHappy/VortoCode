"""任务小组的外部框架成员（ADR-014 S2）：OpenCode / DeepSeek Harness 经 ACP 接成成员。

用一个假的 ACP 框架放在 ``$VORTOONE_WORKER_HOME/bin/opencode``，离线验证：权限按角色判定、框架配置
全部设成「要问」、令牌只在环境里、工作目录用完即删、研究员结果带污点、没装 / 没配端点时如实失败。
"""
import json
import os
import stat
import sys
import textwrap

import pytest

from src.agents import team_engines, team_members
from src.agents.backends.acp import PermissionAsk
from src.workflow.tasks.errors import CollaborationConflict
from src.workflow.tasks.team import TeamService

FAKE = textwrap.dedent(r'''
    import json, os, sys
    def send(msg):
        sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
    def read():
        line = sys.stdin.readline()
        if not line:
            sys.exit(0)
        return json.loads(line)
    def call(method, params, rid):
        send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = read()
            if msg.get("id") == rid and "method" not in msg:
                return msg
    OPTIONS = [{"optionId": "yes", "kind": "allow_once"}, {"optionId": "always", "kind": "allow_always"},
               {"optionId": "no", "kind": "reject_once"}]
    while True:
        msg = read()
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1}})
        elif method == "session/new":
            with open(os.environ["FAKE_LOG"], "w") as log:
                json.dump({"argv": sys.argv, "cwd": params["cwd"], "cwd_exists": os.path.isdir(params["cwd"]),
                           "home": os.environ.get("HOME"), "path0": os.environ["PATH"].split(os.pathsep)[0],
                           "config": os.environ.get("OPENCODE_CONFIG_CONTENT"), "key": os.environ.get("VORTOONE_RELAY_KEY"),
                           "base": os.environ.get("VORTOONE_RELAY_BASE"), "gh": os.environ.get("GH_TOKEN")}, log)
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "s1"}})
        elif method == "session/prompt":
            sid = params["sessionId"]
            picks = {}
            for n, kind in enumerate(["execute", "edit", "fetch", "read"]):
                reply = call("session/request_permission", {"sessionId": sid, "options": OPTIONS,
                             "toolCall": {"toolCallId": f"t{n}", "title": f"do {kind}", "kind": kind}}, 100 + n)
                picks[kind] = reply["result"]["outcome"].get("optionId")
            prompt = params["prompt"][0]["text"]
            text = json.dumps({"picks": picks, "role_prompt_first": prompt.startswith("你是任务小组里的")})
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
                  "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
''')


@pytest.fixture
def engine_home(tmp_path, monkeypatch):
    home = tmp_path / "workers"
    (home / "bin").mkdir(parents=True)
    fake = home / "bin" / "opencode"
    fake.write_text(f"#!{sys.executable}\n" + FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "fake.json"
    monkeypatch.setenv("VORTOONE_WORKER_HOME", str(home))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("OPENAI_API_BASE", "https://relay.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-relay-secret")
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    return home, log


def _team(tmp_path, members):
    service = TeamService(str(tmp_path / "repo"))
    return service, service.create("sid-owner", objective="查资料", acceptance=["给结论并附来源"], members=members)


def test_engine_is_a_closed_choice_and_defaults_to_vortocode(tmp_path):
    service, root = _team(tmp_path, [{"name": "研究员", "role": "web-researcher"},
                                     {"name": "外援", "role": "web-researcher", "engine": "opencode", "model": "qwen3-coder-plus"}])
    assert [item["engine"] for item in root.team["members"]] == ["vortocode", "opencode"]
    child = service.assign(root.id, "外援", "查", "结论")
    assert child.collaboration["dispatch"]["engine"] == "opencode"
    assert child.collaboration["dispatch"]["model"] == "qwen3-coder-plus"
    assert service.view(service.get(root.id))["members"][1]["engine"] == "opencode"
    with pytest.raises(CollaborationConflict, match="未知执行框架"):
        _team(tmp_path, [{"name": "x", "role": "reviewer", "engine": "codex"}])
    with pytest.raises(CollaborationConflict, match="不支持指定模型"):
        _team(tmp_path, [{"name": "x", "role": "reviewer", "engine": "deepseek-harness", "model": "deepseek-v4-pro"}])


def test_role_decides_which_action_kinds_are_allowed():
    researcher, reviewer = team_engines.decide_for("web-researcher"), team_engines.decide_for("reviewer")
    for kind in ("execute", "edit", "delete", "move", "other", "switch_mode"):
        assert not researcher(PermissionAsk(title=kind, kind=kind)) and not reviewer(PermissionAsk(title=kind, kind=kind))
    assert researcher(PermissionAsk(title="f", kind="fetch")) and researcher(PermissionAsk(title="r", kind="read"))
    assert not reviewer(PermissionAsk(title="f", kind="fetch")) and reviewer(PermissionAsk(title="r", kind="read"))
    with pytest.raises(team_engines.EngineUnavailable):
        team_engines.decide_for("developer")


def test_opencode_config_asks_for_everything_and_never_contains_the_key(engine_home):
    env = team_engines.engine_env("opencode", "")
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert "sk-relay-secret" not in env["OPENCODE_CONFIG_CONTENT"]
    assert config["provider"]["vorto"]["options"] == {"baseURL": "{env:VORTOONE_RELAY_BASE}", "apiKey": "{env:VORTOONE_RELAY_KEY}"}
    assert config["model"] == "vorto/deepseek-v4-flash"
    permission = config["permission"]
    assert permission["*"] == "ask" and permission["bash"] == "ask" and permission["edit"] == "ask"
    assert all(permission[key] == "deny" for key in ("task", "external_directory", "question", "skill"))
    assert env["VORTOONE_RELAY_KEY"] == "sk-relay-secret" and "GH_TOKEN" not in env


def test_deepseek_harness_is_read_only_and_uses_the_messages_root(engine_home):
    env = team_engines.engine_env("deepseek-harness", "")
    assert env["DSH_PERMISSION_MODE"] == "read-only"
    assert env["DEEPSEEK_BASE_URL"] == "https://relay.example"
    assert env["DEEPSEEK_API_KEY"] == "sk-relay-secret"
    assert env["HOME"].endswith(os.path.join("workers", "deepseek-harness"))


def test_no_relay_key_means_no_external_member(engine_home, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(team_engines.EngineUnavailable, match="中转站"):
        team_engines.engine_env("opencode", "")


async def test_external_researcher_runs_through_acp_with_role_permissions(tmp_path, engine_home):
    home, log = engine_home
    service, root = _team(tmp_path, [{"name": "外援", "role": "web-researcher", "engine": "opencode"}])
    child = service.assign(root.id, "外援", "查 Python 3.13 的发布日期", "日期和来源")
    done = await team_members.run_member(str(tmp_path / "repo"), root.id, child.id)
    assert done.status == "done", done.error
    report = json.loads(done.result.split("\n\n")[0])
    assert report["picks"] == {"execute": "no", "edit": "no", "fetch": "yes", "read": "yes"}   # 从不选 always
    assert report["role_prompt_first"] is True
    assert "被拒绝的动作" in done.result
    assert done.collaboration["tainted"] is True
    seen = json.loads(log.read_text())
    assert seen["argv"][0] == str(home / "bin" / "opencode")
    assert seen["path0"] == str(home / "bin")                       # 框架同目录的 node 排在前面
    assert seen["home"] == str(home / "opencode")
    assert seen["key"] == "sk-relay-secret" and seen["base"] == "https://relay.example/v1" and seen["gh"] is None
    assert seen["cwd_exists"] is True and seen["cwd"].startswith(str(home / "runs"))
    assert not os.path.exists(seen["cwd"])                           # 专用工作目录用完即删


async def test_external_reviewer_cannot_fetch_and_missing_engine_fails_honestly(tmp_path, engine_home, monkeypatch):
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))            # 这台机器上没有 dsh
    service, root = _team(tmp_path, [{"name": "审", "role": "reviewer", "engine": "opencode"},
                                     {"name": "深", "role": "reviewer", "engine": "deepseek-harness"}])
    child = service.assign(root.id, "审", "审这份材料", "问题清单")
    done = await team_members.run_member(str(tmp_path / "repo"), root.id, child.id)
    assert json.loads(done.result.split("\n\n")[0])["picks"]["fetch"] == "no"
    assert done.collaboration["tainted"] is True                     # 请求过联网（虽被拒）也按污点记
    missing = service.assign(root.id, "深", "审", "问题清单")
    failed = await team_members.run_member(str(tmp_path / "repo"), root.id, missing.id)
    assert failed.status == "failed" and "没有装 dsh" in failed.error


def test_the_approval_card_names_the_external_engine():
    from src.agents.tools.team import _card
    card = _card("查资料", ["给结论"], [{"name": "外援", "role": "web-researcher", "engine": "deepseek-harness"},
                                      {"name": "研究员", "role": "web-researcher"}], "open",
                 {"tasks": 8, "rounds": 12, "timeout_seconds": 3600, "wakes": 16, "parallel": 2})
    assert "外援（联网查资料，由 DeepSeek Harness 执行）" in card
    assert "研究员（联网查资料）" in card


def test_deepseek_harness_tools_are_cut_by_role(engine_home):
    # 它的审批只管越出沙箱的动作，联网和只读命令不来问：只能在工具面上关掉
    reviewer = team_engines.command_for("deepseek-harness", "reviewer")
    researcher = team_engines.command_for("deepseek-harness", "web-researcher")
    assert reviewer[:2] == ["dsh", "--patch"] and reviewer[-2:] == ["--profile", "acp"]
    reviewer_patch, researcher_patch = (open(argv[2], encoding="utf-8").read() for argv in (reviewer, researcher))
    for row in ("tool-bash", "tool-subagent", "tool-workflow", "tool-fs", "tool-skill"):
        assert f"- id: {row}\n  disabled: true" in reviewer_patch and f"- id: {row}\n  disabled: true" in researcher_patch
    assert "- id: tool-web\n" in reviewer_patch and "- id: tool-web\n" not in researcher_patch
    assert team_engines.command_for("opencode", "reviewer") == ["opencode", "acp"]
