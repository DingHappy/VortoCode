"""任务小组的开发成员（ADR-014 S2）：一次性 worktree 里改代码 → VortoOne 验证 → 落本地分支。

假的 ACP 框架放在 ``$VORTOCODE_WORKER_HOME/bin/opencode``：它像 OpenCode 一样先请求权限、获准后自己写文件。
离线验证：只能改 worktree 里的文件、不能执行命令；``.env`` 不进 worktree；改动落成分支并记交付；
返工轮在上一轮分支上继续；只有 Desktop 会话能开这种小组。
"""
import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest

from src.agents import team_delivery, team_engines, team_members
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
    OPTIONS = [{"optionId": "yes", "kind": "allow_once"}, {"optionId": "no", "kind": "reject_once"}]
    cwd = None
    while True:
        msg = read()
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1}})
        elif method == "session/new":
            cwd = params["cwd"]
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "s1"}})
        elif method == "session/prompt":
            sid = params["sessionId"]
            seen = {"env_file": os.path.exists(os.path.join(cwd, ".env")),
                    "before": open(os.path.join(cwd, "app.py")).read()}
            picks = {}
            target = os.path.join(cwd, "app.py")
            asks = [("edit", target, [target]), ("edit", "/etc/hosts", ["/etc/hosts"]),
                    ("execute", "pytest -q", []), ("fetch", "https://example.com", [])]
            for n, (kind, title, locations) in enumerate(asks):
                reply = call("session/request_permission", {"sessionId": sid, "options": OPTIONS, "toolCall": {
                    "toolCallId": f"t{n}", "title": title, "kind": kind,
                    "locations": [{"path": p} for p in locations]}}, 100 + n)
                picks[f"{kind}:{title}"] = reply["result"]["outcome"].get("optionId")
            if picks[f"edit:{target}"] == "yes":
                with open(target, "a") as f:
                    f.write(os.environ.get("FAKE_LINE", "VALUE = 2") + "\n")
            seen["picks"] = picks
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
                  "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": json.dumps(seen)}}}})
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
''')


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "app.py").write_text("VALUE = 1\n")
    (repo / "tests" / "unit" / "test_app.py").write_text("def test_ok():\n    assert True\n")
    (repo / ".gitignore").write_text(".env\n.vortocode/\n")
    (repo / ".worktreeinclude").write_text(".env\n")       # 流水线会照它复制 .env；开发成员不能
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (repo / ".env").write_text("SECRET=1\n")
    home = tmp_path / "workers"
    (home / "bin").mkdir(parents=True)
    fake = home / "bin" / "opencode"
    fake.write_text(f"#!{sys.executable}\n" + FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    registry = tmp_path / "resident.json"
    registry.write_text(json.dumps({"projects": [{"name": "Proj", "repo_root": str(repo), "port": 8099}]}))
    for key, value in {"VORTOCODE_WORKER_HOME": str(home), "OPENAI_API_BASE": "https://relay.example/v1",
                       "OPENAI_API_KEY": "sk-relay", "VORTOCODE_RESIDENT_PROJECTS": str(registry),
                       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(key, value)
    return repo


def _team(tmp_path, repo):
    service = TeamService(str(tmp_path / "assistant"))
    root = service.create("sid-owner", objective="把 VALUE 改成 2", acceptance=["改好并通过测试"], mode="private",
                          members=[{"name": "开发", "role": "developer", "engine": "opencode"},
                                   {"name": "审查员", "role": "reviewer"}],
                          project={"name": "Proj", "repo_root": str(repo)})
    return service, root


def test_developer_teams_are_private_need_a_project_and_only_opencode(tmp_path, project):
    service = TeamService(str(tmp_path / "assistant"))
    dev = [{"name": "开发", "role": "developer", "engine": "opencode"}]
    proj = {"name": "Proj", "repo_root": str(project)}
    with pytest.raises(CollaborationConflict, match="private"):
        service.create("o", objective="x", acceptance=["y"], members=dev, project=proj)
    with pytest.raises(CollaborationConflict, match="登记过的常驻项目"):
        service.create("o", objective="x", acceptance=["y"], members=dev, mode="private")
    with pytest.raises(CollaborationConflict, match="只能由 OpenCode"):
        service.create("o", objective="x", acceptance=["y"], mode="private", project=proj,
                       members=[{"name": "开发", "role": "developer"}])
    with pytest.raises(CollaborationConflict, match="联网成员"):
        service.create("o", objective="x", acceptance=["y"], mode="private", project=proj,
                       members=dev + [{"name": "研究员", "role": "web-researcher"}])
    root = service.create("o", objective="x", acceptance=["y"], mode="private", project=proj, members=dev)
    assert root.team["project"] == proj and service.view(root)["project"] == "Proj"


def test_developer_decisions_stay_inside_the_worktree(tmp_path):
    work = tmp_path / "wt"
    work.mkdir()
    decide = team_engines.decide_for("developer", str(work))
    inside, outside = str(work / "a.py"), str(tmp_path / "other.py")
    assert decide(PermissionAsk(title=inside, kind="edit", locations=[inside]))
    assert decide(PermissionAsk(title="read", kind="read", locations=[inside]))
    assert not decide(PermissionAsk(title=outside, kind="edit", locations=[outside]))
    assert not decide(PermissionAsk(title="x", kind="edit", locations=[inside, outside]))
    assert not decide(PermissionAsk(title="改点东西", kind="edit"))            # 说不清改哪里就不放
    for kind in ("execute", "fetch", "other"):
        assert not decide(PermissionAsk(title="pytest", kind=kind, locations=[inside]))
    with pytest.raises(team_engines.EngineUnavailable):
        team_engines.decide_for("developer")


async def test_developer_round_edits_in_a_worktree_verifies_and_lands_a_branch(tmp_path, project):
    service, root = _team(tmp_path, project)
    child = service.assign(root.id, "开发", "把 app.py 的 VALUE 改成 2", "改动说明")
    calls = []

    def fake_tests(path, cmd):
        calls.append((str(path), list(cmd), open(os.path.join(path, "app.py")).read()))
        return {"ok": True, "output": "1 passed", "cmd": " ".join(cmd)}

    done = await team_members.run_member(str(tmp_path / "assistant"), root.id, child.id, run_tests=fake_tests)
    assert done.status == "done", done.error
    seen = json.loads(done.result.split("\n\n")[0])
    assert seen["env_file"] is False                                    # .env 不进 worktree
    picks = seen["picks"]
    assert [value for key, value in picks.items() if key.startswith("edit:") and key.endswith("app.py")] == ["yes"]
    assert picks["edit:/etc/hosts"] == "no" and picks["execute:pytest -q"] == "no" and picks["fetch:https://example.com"] == "no"
    branch = team_delivery.branch_name(child.id, 1)
    assert "【交付】分支 " + branch in done.result and "验证：通过" in done.result
    assert calls and calls[0][1][-3:] == ["pytest", "-q", "tests/unit"] and "VALUE = 2" in calls[0][2]
    assert "VALUE = 2" in _git(project, "show", f"{branch}:app.py")
    assert _git(project, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert "VALUE = 2" not in (project / "app.py").read_text()          # 主工作区没动
    assert not (project / ".vortocode" / "worktrees" / f"team-{child.id}-r1").exists()
    delivery = service.ledger.load(child.id).collaboration["delivery"]
    assert delivery["branch"] == branch and delivery["files"] == ["app.py"] and delivery["verified"] is True
    assert done.collaboration["tainted"] is True                        # 请求过执行 / 联网（虽被拒）

    # 返工：第二轮在上一轮分支上继续，落到新分支
    service.review(root.id, child.id, "rework", "再加一行")
    os.environ["FAKE_LINE"] = "EXTRA = 3"
    try:
        again = await team_members.run_member(str(tmp_path / "assistant"), root.id, child.id, run_tests=fake_tests)
    finally:
        del os.environ["FAKE_LINE"]
    assert again.status == "done", again.error
    assert "VALUE = 2" in json.loads(again.result.split("\n\n")[0])["before"]
    second = _git(project, "show", f"{team_delivery.branch_name(child.id, 2)}:app.py")
    assert "VALUE = 2" in second and "EXTRA = 3" in second
    assert "EXTRA = 3" in team_delivery.branch_diff(str(project), team_delivery.branch_name(child.id, 2))


async def test_failed_verification_is_reported_and_no_edit_means_no_branch(tmp_path, project, monkeypatch):
    service, root = _team(tmp_path, project)
    child = service.assign(root.id, "开发", "改", "说明")
    done = await team_members.run_member(str(tmp_path / "assistant"), root.id, child.id,
                                         run_tests=lambda path, cmd: {"ok": False, "output": "1 failed: boom", "cmd": "pytest"})
    assert done.status == "done" and "验证：没通过" in done.result and "1 failed: boom" in done.result

    other = service.assign(root.id, "开发", "改", "说明")

    async def lazy(engine, role, model, prompt, **kwargs):
        from src.agents.backends.acp import AcpResult
        return AcpResult(text="看了一下，不用改", stop_reason="end_turn")
    nothing = await team_members.run_member(str(tmp_path / "assistant"), root.id, other.id, run_external=lazy)
    assert nothing.status == "failed" and "没有改动任何文件" in nothing.error
    assert team_delivery.branch_name(other.id, 1) not in _git(project, "branch", "--list")


async def test_only_desktop_sessions_can_open_a_developer_team(tmp_path, project):
    from src.agents.tools.team import build_team_tools
    from src.scheduling import team_runner

    class Runner:
        woke = []

        def wake(self, root_id, event):
            self.woke.append(root_id)
    team_runner.set_team_runner(Runner())
    try:
        async def yes(*_args, **_kwargs):
            return True
        args = {"objective": "改代码", "acceptance": ["测试通过"], "mode": "private", "project": "proj",
                "members": [{"name": "开发", "role": "developer", "engine": "opencode"}]}
        phone = {t.name: t for t in build_team_tools(str(tmp_path / "a"), confirm=yes, owner="o")}
        assert "只能在 Desktop" in await phone["team_propose"].handler(args)
        desk = {t.name: t for t in build_team_tools(str(tmp_path / "a"), confirm=yes, owner="o", allow_developers=True)}
        assert "Proj" in await desk["team_propose"].handler({**args, "project": "nope"})
        assert "小组已开工" in await desk["team_propose"].handler(args)
        root = TeamService(str(tmp_path / "a")).list()[0]
        assert root.team["project"]["repo_root"] == str(project)
    finally:
        team_runner.set_team_runner(None)


def test_verification_discounts_failures_that_also_fail_before_the_change(tmp_path, project, monkeypatch):
    from src.agents import worktree
    path = team_delivery.prepare(str(project), "task-x", 1)
    try:
        (path / "app.py").write_text("VALUE = 2\n")
        red = {"ok": False, "cmd": "python -m pytest -q tests/unit",
               "output": "FAILED tests/unit/test_a.py::test_x - boom\n1 failed, 9 passed in 1.00s"}
        monkeypatch.setattr(worktree, "run_tests", lambda *a, **k: dict(red))
        monkeypatch.setattr(worktree, "retry_failed", lambda p, result: result)
        monkeypatch.setattr(worktree, "compare_with_base", lambda root, base, result: {
            **result, "ok": True, "baseline": {"base": base, "preexisting": ["tests/unit/test_a.py::test_x"], "new": []}})
        delivery = team_delivery.finish_round(str(project), path, child_id="task-x", number=1, objective="改")
        assert delivery["verified"] is True and delivery["preexisting_failures"] == 1
        assert "另有 1 个测试在改动前的代码上同样失败" in team_delivery.describe(delivery, "Proj")

        # 输出被截断：汇总说 3 个失败，只解析出 1 个——不改判
        truncated = {**red, "output": "FAILED tests/unit/test_a.py::test_x - boom\n3 failed, 7 passed in 1.00s"}
        monkeypatch.setattr(worktree, "run_tests", lambda *a, **k: dict(truncated))
        assert team_delivery._verify(str(project), path, ["pytest"])["ok"] is False
    finally:
        team_delivery.cleanup(str(project), path)
