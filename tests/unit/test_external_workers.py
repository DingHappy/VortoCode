"""外部执行者（Codex / Claude Code CLI）适配层。

事件样本录自真实 CLI（codex-cli 0.162、Claude Code 2.1，2026-10-17），路径已脱敏；
运行测试用一个假 CLI 脚本，全程离线、不碰真实账号。
"""
import os
import stat
import subprocess
import sys

import pytest

from src.agents import external_workers as ew
from src.agents import taint

CODEX_EVENTS = [
    {"type": "thread.started", "thread_id": "01a1493e-fe25-7ca3-8756-ac7af0dba36c"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
                                        "text": "I’ll read a.txt, then create b.txt containing hello.\n"}},
    {"type": "item.started", "item": {"id": "item_1", "type": "command_execution",
                                      "command": "/bin/zsh -lc 'cat a.txt'", "aggregated_output": "",
                                      "exit_code": None, "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution",
                                        "command": "/bin/zsh -lc 'cat a.txt'", "aggregated_output": "x\n",
                                        "exit_code": 0, "status": "completed"}},
    {"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "DONE"}},
    {"type": "turn.completed", "usage": {"input_tokens": 49313, "cached_input_tokens": 37760,
                                         "output_tokens": 77}},
]

CLAUDE_EVENTS = [
    {"type": "system", "subtype": "hook_started", "session_id": "65827879-601e-4d12-aa3c-b63d87ad6693"},
    {"type": "system", "subtype": "init", "session_id": "65827879-601e-4d12-aa3c-b63d87ad6693"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/w/a.txt"}}]}},
    {"type": "user", "message": {"content": [{"tool_use_id": "toolu_1", "type": "tool_result",
                                              "content": "1\tx\n"}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_2", "name": "Write",
         "input": {"file_path": "/w/c.txt", "content": "hi"}}]}},
    {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": ""},
                                                  {"type": "text", "text": "DONE"}]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "DONE",
     "session_id": "65827879-601e-4d12-aa3c-b63d87ad6693", "total_cost_usd": 0.11,
     "permission_denials": []},
]


def _fold(worker, events):
    run = ew.WorkerRun(worker)
    parse = ew.parse_codex_event if worker == "codex" else ew.parse_claude_event
    for obj in events:
        for event in parse(obj):
            ew.fold_event(run, event)
    return run


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    taint.reset_taint()
    for name in (ew.ROLE_ENV, ew.TIMEOUT_ENV, "VORTOCODE_CODEX_BIN", "VORTOCODE_CLAUDE_BIN"):
        monkeypatch.delenv(name, raising=False)
    yield
    taint.reset_taint()


# ── 配置 ──

def test_role_table_accepts_only_known_roles_and_workers():
    assert ew.parse_role_workers("implement=codex, review=CLAUDE") == {"implement": "codex", "review": "claude"}
    assert ew.parse_role_workers("implement=gpt,deploy=codex,,review") == {}


def test_no_config_keeps_internal_agents(monkeypatch, tmp_path):
    assert ew.external_agent_for_role("implement", str(tmp_path)) is None
    monkeypatch.setenv(ew.ROLE_ENV, "review=claude")
    assert ew.external_agent_for_role("implement", str(tmp_path)) is None
    reviewer = ew.external_agent_for_role("review", str(tmp_path))
    assert reviewer.worker == "claude" and reviewer.write is False


# ── 权限参数 ──

@pytest.mark.parametrize("worker", ew.WORKERS)
@pytest.mark.parametrize("write", [True, False])
def test_argv_never_bypasses_sandbox_or_permissions(worker, write):
    argv = ew.build_argv(worker, cwd="/w", write=write, system="rules")
    joined = " ".join(argv).lower()
    for banned in ("dangerously", "danger-full-access", "bypasspermissions", "approve-for-me"):
        assert banned not in joined
    assert "do it" not in joined                    # 提示词只走 stdin，不进 argv
    assert ew.stdin_text(worker, "do it", "rules").endswith("do it")


def test_codex_sandbox_follows_role():
    write = ew.build_argv("codex", cwd="/w", write=True)
    read = ew.build_argv("codex", cwd="/w", write=False)
    assert write[write.index("-s") + 1] == "workspace-write"
    assert read[read.index("-s") + 1] == "read-only"
    assert write[write.index("-C") + 1] == "/w"
    assert "mcp_servers={}" in write and write[-1] == "-"
    assert ew.stdin_text("codex", "task", "rules").startswith("rules")


def test_claude_reviewer_gets_no_edit_tools_and_ignores_user_settings():
    read = ew.build_argv("claude", cwd="/w", write=False, system="rules")
    write = ew.build_argv("claude", cwd="/w", write=True)
    tools = read[read.index("--allowedTools") + 1:]
    assert "Edit" not in tools and "Write" not in tools and "Read" in tools
    assert "Edit" in write[write.index("--allowedTools") + 1:]
    assert read[read.index("--append-system-prompt") + 1] == "rules"
    assert ew.stdin_text("claude", "task", "rules") == "task"
    assert read[read.index("--permission-mode") + 1] == "dontAsk"
    assert read[read.index("--setting-sources") + 1] == "local"
    assert "--strict-mcp-config" in read


def test_worker_env_strips_relay_and_repo_tokens(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    monkeypatch.setenv("GH_TOKEN", "g")
    env = ew.worker_env()
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env and "GH_TOKEN" not in env
    assert env.get("PATH") == os.environ["PATH"]


# ── 事件解析（真实样本） ──

def test_codex_events_fold_into_conclusion_session_and_commands():
    run = _fold("codex", CODEX_EVENTS)
    assert run.final_text == "DONE"
    assert run.session_id == "01a1493e-fe25-7ca3-8756-ac7af0dba36c"
    commands = [e for e in run.events if e.kind == "command"]
    assert len(commands) == 1 and commands[0].data["exit_code"] == 0   # started 事件不重复计
    assert run.usage["output_tokens"] == 77 and not run.error and not run.used_network


def test_claude_events_fold_into_conclusion_cost_and_file_changes():
    run = _fold("claude", CLAUDE_EVENTS)
    assert run.final_text == "DONE" and run.cost_usd == 0.11
    assert run.session_id == "65827879-601e-4d12-aa3c-b63d87ad6693"
    assert [e.text for e in run.events if e.kind == "file"] == ["/w/c.txt"]
    assert not run.error and not run.used_network


def test_failures_and_network_use_are_reported():
    run = _fold("codex", [{"type": "turn.failed", "error": {"message": "quota exceeded"}}])
    assert run.error == "quota exceeded"
    run = _fold("claude", [{"type": "result", "subtype": "error_max_turns", "is_error": True,
                            "session_id": "s"}])
    assert run.error and run.session_id == "s"
    run = _fold("codex", [{"type": "item.completed", "item": {"type": "web_search", "query": "x"}}])
    assert run.used_network
    run = _fold("claude", [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://x"}}]}}])
    assert run.used_network


# ── 运行（假 CLI） ──

_FAKE = """#!{python}
import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "ok")
prompt = sys.stdin.read()
worker = os.path.basename(sys.argv[0]).split("-")[0]
def emit(obj):
    print(json.dumps(obj), flush=True)
if mode == "sleep":
    time.sleep(30)
if mode == "fail":
    print("login required", file=sys.stderr)
    sys.exit(3)
if os.environ.get("FAKE_LEAK"):
    emit({{"type": "item.completed", "item": {{"type": "agent_message",
          "text": "env:" + ",".join(k for k in ("OPENAI_API_KEY", "GITHUB_TOKEN") if k in os.environ)}}}})
if worker == "codex":
    write = sys.argv[sys.argv.index("-s") + 1] == "workspace-write"
    emit({{"type": "thread.started", "thread_id": "t-1"}})
    if write:
        open("impl.txt", "w").write("done\\n")
        emit({{"type": "item.completed", "item": {{"type": "file_change", "changes": [{{"path": "impl.txt"}}]}}}})
    if mode == "web":
        emit({{"type": "item.completed", "item": {{"type": "web_search", "query": "q"}}}})
    emit({{"type": "item.completed", "item": {{"type": "agent_message", "text": "got:" + prompt[-12:]}}}})
    emit({{"type": "item.completed", "item": {{"type": "agent_message", "text": "codex finished"}}}})
    emit({{"type": "turn.completed", "usage": {{"output_tokens": 1}}}})
else:
    emit({{"type": "system", "subtype": "init", "session_id": "c-1"}})
    emit({{"type": "result", "subtype": "success", "is_error": False, "result": "[]", "session_id": "c-1"}})
"""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for worker in ew.WORKERS:
        path = bindir / f"{worker}-fake"
        path.write_text(_FAKE.format(python=sys.executable))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv(f"VORTOCODE_{worker.upper()}_BIN", str(path))
    return bindir


async def test_run_worker_success_collects_conclusion(fake_cli, tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    seen = []
    run = await ew.run_worker("codex", "go", str(work), write=True, on_event=seen.append)
    assert run.ok and run.final_text == "codex finished" and run.session_id == "t-1"
    assert (work / "impl.txt").read_text() == "done\n"
    assert any(e.kind == "file" for e in seen)


async def test_large_prompt_goes_through_stdin(fake_cli, tmp_path):
    big = "x" * 2_000_000 + "END-OF-DIFF"           # 远超 ARG_MAX：只有走 stdin 才能送达
    run = await ew.run_worker("codex", big, str(tmp_path), write=False)
    assert run.ok and any(e.text == "got:" + big[-12:] for e in run.events)


async def test_run_worker_reports_exit_code_and_stderr(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    run = await ew.run_worker("claude", "go", str(tmp_path), write=False)
    assert not run.ok and "退出码 3" in run.error and "login required" in run.error


async def test_run_worker_timeout_kills_process(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    run = await ew.run_worker("codex", "go", str(tmp_path), write=False, timeout=1)
    assert not run.ok and "超时" in run.error


async def test_run_worker_child_never_sees_secrets(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LEAK", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    run = await ew.run_worker("codex", "go", str(tmp_path), write=False)
    leak = next(e.text for e in run.events if e.text.startswith("env:"))
    assert leak == "env:"


async def test_missing_binary_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    run = await ew.run_worker("codex", "go", str(tmp_path), write=False)
    assert not run.ok and "找不到 codex" in run.error


# ── Agent 适配 ──

async def test_agent_refuses_unattended_profile(fake_cli, tmp_path):
    from src.agents.capabilities import SessionCapabilities
    caps = SessionCapabilities.for_profile("unattended", str(tmp_path))
    agent = ew.ExternalWorkerAgent("codex", str(tmp_path), write=True, capabilities=caps)
    with pytest.raises(ew.ExternalWorkerError, match="无人值守"):
        await agent.run_turn("go")
    assert not (tmp_path / "impl.txt").exists()


async def test_agent_failure_raises_like_llm_channel_error(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    agent = ew.ExternalWorkerAgent("claude", str(tmp_path), write=False)
    with pytest.raises(ew.ExternalWorkerError, match="退出码"):
        await agent.run_turn("go")


async def test_agent_marks_taint_only_when_worker_went_online(fake_cli, tmp_path, monkeypatch):
    agent = ew.ExternalWorkerAgent("codex", str(tmp_path), write=False)
    assert await agent.run_turn("go") == "codex finished"
    assert not taint.is_tainted()
    monkeypatch.setenv("FAKE_MODE", "web")
    await agent.run_turn("go")
    assert taint.is_tainted()


async def test_plan_mode_never_writes(fake_cli, tmp_path):
    agent = ew.ExternalWorkerAgent("codex", str(tmp_path), write=True)
    await agent.run_turn("go", mode="plan")
    assert not (tmp_path / "impl.txt").exists()


def _git_repo(path):
    path.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=path, check=True)
    (path / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


async def test_isolated_task_collects_external_workers_diff(fake_cli, tmp_path):
    from src.agents.worktree import run_isolated_task
    repo = _git_repo(tmp_path / "repo")
    diff, conclusion, _ = await run_isolated_task(
        str(repo), "wt-ext", "implement it",
        lambda wt: ew.ExternalWorkerAgent("codex", wt, write=True))
    assert conclusion == "codex finished"
    assert "impl.txt" in diff and "+done" in diff
    assert not (repo / "impl.txt").exists()            # 主工作区不被碰


async def test_dev_isolated_routes_implementer_to_configured_worker(fake_cli, tmp_path, monkeypatch):
    """装配真 dev 工具：配了 implement=codex，流水线交给 run_isolated_task 的就是外部执行者。"""
    import src.agents.worktree as worktree
    from src.agents.main_agent import build_dev_tools

    monkeypatch.setenv(ew.ROLE_ENV, "implement=codex")
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "1")
    built = []

    async def fake_run_isolated_task(_repo_root, _wid, desc, build_agent, test_cmd=None, **_kw):
        work = tmp_path / "wt"
        work.mkdir(exist_ok=True)
        agent = build_agent(str(work))
        built.append(agent)
        return "", await agent.run_turn(desc), None

    monkeypatch.setattr(worktree, "run_isolated_task", fake_run_isolated_task)
    progress = []
    tools = {t.name: t for t in build_dev_tools(str(tmp_path), on_progress=progress.append)}
    await tools["dev_isolated"].handler({"description": "add impl"})
    assert built and isinstance(built[0], ew.ExternalWorkerAgent) and built[0].write
    assert any(line.startswith("[codex] 改动") for line in progress)
