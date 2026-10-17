"""外部执行者（Codex / Claude Code CLI）适配层。

事件样本录自真实 CLI（codex-cli 0.162、Claude Code 2.1，2026-10-17），路径已脱敏；
运行测试用一个假 CLI 脚本，全程离线、不碰真实账号。
"""
import json
import os
import stat
import subprocess
import sys

import pytest

from src.agents import external_workers as ew

ew_search = ew._search_dirs
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
def _clean(monkeypatch, tmp_path_factory):
    taint.reset_taint()
    for name in (ew.ROLE_ENV, ew.TIMEOUT_ENV, ew.WORKER_HOME_ENV, "VORTOCODE_CODEX_BIN", "VORTOCODE_CLAUDE_BIN"):
        monkeypatch.delenv(name, raising=False)
    ew._SUMMARY_CACHE.clear()
    ew._COOLDOWN_UNTIL.clear()
    # 测试绝不读真实的 ~/.codex / ~/.claude，也不能在 ~/.nvm 等兜底位置找到真 CLI：HOME 一并指到空目录
    empty = tmp_path_factory.mktemp("cli-home")
    monkeypatch.setenv("HOME", str(empty))
    monkeypatch.setenv("CODEX_HOME", str(empty))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(empty))
    monkeypatch.setattr(ew, "_search_dirs", lambda: [] if os.environ.get("HOME") == str(empty) else ew_search(), raising=True)
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
if sys.argv[1:3] in (["login", "status"], ["auth", "status"]):
    out = os.environ.get("FAKE_LOGGED_OUT")
    if sys.argv[1] == "login":
        print("Not logged in" if out else "Logged in using ChatGPT")
    else:
        print(json.dumps({{"loggedIn": not out, "authMethod": "claude.ai"}}))
    sys.exit(1 if out else 0)
prompt = sys.stdin.read()
worker = os.path.basename(sys.argv[0]).split("-")[0]
def emit(obj):
    print(json.dumps(obj), flush=True)
if mode == "sleep":
    time.sleep(30)
if mode == "fail":
    print("login required", file=sys.stderr)
    sys.exit(3)
if mode == "limit":
    print("You've hit your usage limit. Try again later.", file=sys.stderr)
    sys.exit(1)
if mode == "crash":
    print("Traceback: SyntaxError in generated file", file=sys.stderr)
    sys.exit(1)
if os.environ.get("FAKE_LEAK"):
    emit({{"type": "item.completed", "item": {{"type": "agent_message",
          "text": "env:" + ",".join(k for k in ("OPENAI_API_KEY", "GITHUB_TOKEN") if k in os.environ)}}}})
if worker == "codex":
    write = sys.argv[sys.argv.index("-s") + 1] == "workspace-write"
    emit({{"type": "thread.started", "thread_id": "t-1"}})
    if write:
        open("impl.txt", "w").write("done\\n")
        emit({{"type": "item.completed", "item": {{"type": "file_change", "changes": [{{"path": "impl.txt"}}]}}}})
    if mode == "home":
        emit({{"type": "item.completed", "item": {{"type": "agent_message",
              "text": "home:" + os.environ.get("CODEX_HOME", "") + "|" + os.environ.get("CLAUDE_CONFIG_DIR", "")}}}})
    if mode == "web":
        emit({{"type": "item.completed", "item": {{"type": "web_search", "query": "q"}}}})
    emit({{"type": "item.completed", "item": {{"type": "agent_message", "text": "got:" + prompt[-12:]}}}})
    emit({{"type": "item.completed", "item": {{"type": "agent_message",
          "text": "rules:" + str("VORTO_QUESTION" in prompt)}}}})
    if mode == "ask":
        emit({{"type": "item.completed", "item": {{"type": "agent_message", "text":
              'I cannot tell which rounding to use.\\nVORTO_QUESTION {{"question": "Round half up or half even?", '
              '"options": ["half up", "half even"], "context": "calc.py has no rounding yet"}}'}}}})
        emit({{"type": "turn.completed", "usage": {{}}}})
        sys.exit(0)
    emit({{"type": "item.completed", "item": {{"type": "agent_message", "text": "codex finished"}}}})
    emit({{"type": "turn.completed", "usage": {{"output_tokens": 1}}}})
else:
    emit({{"type": "system", "subtype": "init", "session_id": "c-1"}})
    seen = ",".join(k for k in ("ANTHROPIC_BASE_URL", "GITHUB_TOKEN", "OPENAI_API_KEY") if k in os.environ)
    emit({{"type": "result", "subtype": "success", "is_error": False,
          "result": ("env:" + seen) if mode == "env" else "[]", "session_id": "c-1"}})
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


# ── 提问（开发任务的 ask_task_question 通道） ──

def test_parse_question_line_variants():
    q = ew.parse_question('x\nVORTO_QUESTION {"question": "Q?", "options": ["a","b","c","d"], "context": "c"}')
    assert q == {"question": "Q?", "options": ["a", "b", "c"], "context": "c"}
    assert ew.parse_question("`VORTO_QUESTION: {\"question\": \"Q2\"}`")["question"] == "Q2"
    assert ew.parse_question("VORTO_QUESTION which file?")["question"] == "which file?"   # JSON 坏了也不丢
    assert ew.parse_question("all done") is None
    assert ew.parse_question('VORTO_QUESTION {"question": ""}') is None


class _Binding:
    def __init__(self, *, with_tool=True, tainted=False):
        from src.agents.tool import Tool, ToolTurnYield
        self.asked = []

        async def ask(args):
            self.asked.append(args)
            raise ToolTurnYield("开发问题已保存")

        self.tool = Tool("ask_task_question", "ask", {}, ask, yields_turn=True) if with_tool else None
        self.context, self.tainted = "", tainted


async def test_worker_question_goes_through_the_task_question_tool(fake_cli, tmp_path, monkeypatch):
    from src.agents.tool import ToolTurnYield
    monkeypatch.setenv("FAKE_MODE", "ask")
    binding, progress = _Binding(), []
    agent = ew.ExternalWorkerAgent("codex", str(tmp_path), write=True, question=binding,
                                   on_progress=progress.append)
    with pytest.raises(ToolTurnYield):
        await agent.run_turn("implement rounding")
    assert binding.asked == [{"question": "Round half up or half even?",
                              "options": ["half up", "half even"], "context": "calc.py has no rounding yet"}]
    assert any(e.text == "rules:True" for e in agent.last_run.events)     # 只有能提问时才教它怎么问
    assert any(line.startswith("[codex] 提问") for line in progress)


async def test_question_rules_only_when_a_question_channel_exists(fake_cli, tmp_path):
    agent = ew.ExternalWorkerAgent("codex", str(tmp_path), write=True, question=_Binding(with_tool=False))
    await agent.run_turn("go")
    assert any(e.text == "rules:False" for e in agent.last_run.events)


async def test_question_without_channel_fails_instead_of_passing_as_conclusion(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ask")
    agent = ew.ExternalWorkerAgent("codex", str(tmp_path), write=True)
    with pytest.raises(ew.ExternalWorkerError, match="不能提问"):
        await agent.run_turn("go")


async def test_tainted_question_history_keeps_turn_tainted(fake_cli, tmp_path):
    agent = ew.ExternalWorkerAgent("codex", str(tmp_path), write=True, question=_Binding(tainted=True))
    await agent.run_turn("go")
    assert taint.is_tainted()


def test_reviewer_never_gets_a_question_channel(monkeypatch, tmp_path):
    monkeypatch.setenv(ew.ROLE_ENV, "implement=codex,review=claude")
    binding = _Binding()
    assert ew.external_agent_for_role("implement", str(tmp_path), question=binding).question is binding
    assert ew.external_agent_for_role("review", str(tmp_path), question=binding).question is None


async def test_dependent_block_question_suspends_and_leaves_branch_untouched(fake_cli, tmp_path, monkeypatch):
    from src.agents.tool import ToolTurnYield
    from src.agents.worktree import run_dependent_on_branch
    repo = _git_repo(tmp_path / "repo")
    subprocess.run(["git", "branch", "vorto/q"], cwd=repo, check=True)
    before = subprocess.run(["git", "rev-parse", "vorto/q"], cwd=repo, capture_output=True, text=True).stdout
    monkeypatch.setenv("FAKE_MODE", "ask")
    binding = _Binding()
    with pytest.raises(ToolTurnYield):
        await run_dependent_on_branch(str(repo), "wt-q", "vorto/q", "implement rounding",
                                      lambda wt: ew.ExternalWorkerAgent("codex", wt, write=True, question=binding),
                                      "msg", None)
    after = subprocess.run(["git", "rev-parse", "vorto/q"], cwd=repo, capture_output=True, text=True).stdout
    assert binding.asked and before == after


# ── 个人配置：隔离 + 只透传模型选择 ──

_CODEX_CONFIG = """
model = "gpt-x"
model_reasoning_effort = "high"
model_provider = "deep"
notify = ["/bin/say", "done"]

[features]
hooks = true
memories = true

[model_providers.deep]
name = "DeepSeek"
base_url = "https://api.example.test/v1"
env_key = "DEEP_API_KEY"
wire_api = "chat"
http_headers = { "X-Org" = "me" }

[model_providers."bad id"]
base_url = "https://evil.example.test"

[mcp_servers.x]
command = "evil"
"""


def test_codex_passes_only_model_choice_from_user_config(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(_CODEX_CONFIG)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    args = ew.codex_user_overrides()
    pairs = [args[i + 1] for i in range(0, len(args), 2)]
    assert args[::2] == ["-c"] * len(pairs)
    assert 'model="gpt-x"' in pairs and 'model_reasoning_effort="high"' in pairs and 'model_provider="deep"' in pairs
    assert 'model_providers.deep.base_url="https://api.example.test/v1"' in pairs
    assert 'model_providers.deep.http_headers={ "X-Org" = "me" }' in pairs
    joined = " ".join(pairs)
    for leaked in ("notify", "features", "hooks", "memories", "mcp_servers", "evil"):
        assert leaked not in joined


def test_codex_argv_ignores_user_config_and_does_not_persist_sessions():
    argv = ew.build_argv("codex", cwd="/w", write=True, extra=["-c", 'model="gpt-x"'])
    assert "--ignore-user-config" in argv and "--ephemeral" in argv
    assert argv[-3:] == ["-c", 'model="gpt-x"', "-"]


def test_broken_or_missing_codex_config_means_cli_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert ew.codex_user_overrides() == []
    (tmp_path / "config.toml").write_text("model = [unclosed")
    assert ew.codex_user_overrides() == []


def test_claude_passes_model_and_env_but_not_hooks_or_permissions(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text(json.dumps({
        "model": "opus", "apiKeyHelper": "/usr/local/bin/key-helper",
        "env": {"ANTHROPIC_BASE_URL": "https://glm.example.test/anthropic", "ANTHROPIC_AUTH_TOKEN": "t",
                "GITHUB_TOKEN": "g", "OPENAI_API_KEY": "k", "bad name": "x"},
        "hooks": {"PreToolUse": [{"hooks": [{"command": "evil"}]}]},
        "permissions": {"allow": ["Bash(*)"]}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    args, env = ew.user_config("claude")
    assert args[:2] == ["--model", "opus"]
    assert json.loads(args[args.index("--settings") + 1]) == {"apiKeyHelper": "/usr/local/bin/key-helper"}
    assert env == {"ANTHROPIC_BASE_URL": "https://glm.example.test/anthropic", "ANTHROPIC_AUTH_TOKEN": "t"}
    assert "evil" not in " ".join(args) and "Bash(*)" not in " ".join(args)
    argv = ew.build_argv("claude", cwd="/w", write=False, extra=args)
    assert "--no-session-persistence" in argv and argv[argv.index("--setting-sources") + 1] == "local"


async def test_third_party_claude_env_reaches_the_child_without_tokens(fake_cli, tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text(json.dumps({"env": {
        "ANTHROPIC_BASE_URL": "https://glm.example.test/anthropic", "GITHUB_TOKEN": "g"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("FAKE_MODE", "env")
    run = await ew.run_worker("claude", "go", str(tmp_path), write=False)
    assert run.ok and run.final_text == "env:ANTHROPIC_BASE_URL"


# ── doctor ──

def test_doctor_reports_unconfigured_as_fine():
    from src.gateway.doctor import _check_external_workers
    check = _check_external_workers()
    assert check.level == "ok" and "未配置" in check.detail


def test_doctor_reports_login_and_model(fake_cli, tmp_path, monkeypatch):
    from src.gateway.doctor import _check_external_workers
    (tmp_path / "config.toml").write_text('model = "gpt-x"\n')
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv(ew.ROLE_ENV, "implement=codex,review=claude")
    check = _check_external_workers()
    assert check.level == "ok", check.detail
    assert "implement=codex（Logged in using ChatGPT，模型 gpt-x）" in check.detail
    assert "review=claude（claude.ai，模型 CLI 默认）" in check.detail


def test_doctor_fails_when_logged_out_or_missing(fake_cli, tmp_path, monkeypatch):
    from src.gateway.doctor import _check_external_workers
    monkeypatch.setenv(ew.ROLE_ENV, "implement=codex")
    monkeypatch.setenv("FAKE_LOGGED_OUT", "1")
    check = _check_external_workers()
    assert check.level == "fail" and "codex login" in check.detail
    monkeypatch.setenv("VORTOCODE_CODEX_BIN", str(tmp_path / "nope"))
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("VORTOCODE_CODEX_BIN")
    check = _check_external_workers()
    assert check.level == "fail" and "找不到命令" in check.detail


# ── 两种模式：本机（个人登录）/ 常驻（专用目录），不混 ──

def test_resident_mode_never_reads_personal_cli_config(tmp_path, monkeypatch):
    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "config.toml").write_text('model = "personal-model"\n')
    (personal / "settings.json").write_text(json.dumps({"model": "personal-opus", "env": {"ANTHROPIC_BASE_URL": "https://p"}}))
    monkeypatch.setenv("CODEX_HOME", str(personal))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(personal))
    assert ew.worker_mode() == "local" and 'model="personal-model"' in ew.codex_user_overrides()

    dedicated = tmp_path / "workers"
    monkeypatch.setenv(ew.WORKER_HOME_ENV, str(dedicated))
    assert ew.worker_mode() == "resident"
    assert ew.codex_user_overrides() == [] and ew.user_config("claude") == ([], {})
    (dedicated / "codex").mkdir(parents=True)
    (dedicated / "codex" / "config.toml").write_text('model = "server-model"\n')
    assert ew.codex_user_overrides() == ["-c", 'model="server-model"']   # 常驻电脑自己的配置照样生效


def test_resident_mode_points_both_clis_at_private_dedicated_dirs(tmp_path, monkeypatch):
    dedicated = tmp_path / "workers"
    monkeypatch.setenv(ew.WORKER_HOME_ENV, str(dedicated))
    env = ew.worker_env()
    assert env["CODEX_HOME"] == str(dedicated / "codex") and env["CLAUDE_CONFIG_DIR"] == str(dedicated / "claude")
    for sub in ("codex", "claude"):
        assert (dedicated / sub).stat().st_mode & 0o777 == 0o700
    monkeypatch.delenv(ew.WORKER_HOME_ENV)
    assert ew.mode_env() == {}                                        # 本机模式不改 CLI 的 home


async def test_resident_child_runs_against_dedicated_home(fake_cli, tmp_path, monkeypatch):
    dedicated = tmp_path / "workers"
    monkeypatch.setenv(ew.WORKER_HOME_ENV, str(dedicated))
    monkeypatch.setenv("FAKE_MODE", "home")
    run = await ew.run_worker("codex", "go", str(tmp_path), write=False)
    assert f"home:{dedicated / 'codex'}|{dedicated / 'claude'}" in [e.text for e in run.events]


def test_doctor_names_the_mode_and_resident_login_command(fake_cli, tmp_path, monkeypatch):
    from src.gateway.doctor import _check_external_workers
    monkeypatch.setenv(ew.ROLE_ENV, "implement=codex")
    assert _check_external_workers().detail.startswith("本机模式·个人登录")
    monkeypatch.setenv(ew.WORKER_HOME_ENV, str(tmp_path / "workers"))
    monkeypatch.setenv("FAKE_LOGGED_OUT", "1")
    check = _check_external_workers()
    assert check.detail.startswith("常驻模式·专用登录") and "vc workers login codex" in check.detail


def test_workers_login_only_writes_into_the_dedicated_home(fake_cli, tmp_path, monkeypatch, capsys):
    import subprocess
    from src import cli
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, env=None, **_: calls.append((argv, env)) or
                        type("R", (), {"returncode": 0})())
    assert cli.run_workers_cli("login", "codex") == 2 and not calls     # 本机模式：不替你改个人配置
    assert "模式：本机" in capsys.readouterr().out
    dedicated = tmp_path / "workers"
    monkeypatch.setenv(ew.WORKER_HOME_ENV, str(dedicated))
    assert cli.run_workers_cli("login", "codex") == 0
    argv, env = calls[0]
    assert argv[1:] == ["login", "--device-auth"] and env["CODEX_HOME"] == str(dedicated / "codex")


async def test_capabilities_expose_mode_and_status_without_credentials(fake_cli, tmp_path, monkeypatch):
    from src.web.routers.delegations import capabilities
    monkeypatch.setenv(ew.ROLE_ENV, "implement=codex,review=claude")
    monkeypatch.setenv(ew.WORKER_HOME_ENV, str(tmp_path / "workers"))
    summary = (await capabilities())["external_workers"]
    assert summary["mode"] == "resident" and summary["roles"] == {"implement": "codex", "review": "claude"}
    assert {w["worker"]: w["logged_in"] for w in summary["workers"]} == {"claude": True, "codex": True}
    assert set(summary["workers"][0]) == {"worker", "found", "path", "logged_in", "auth", "model", "provider"}


def test_summary_cache_drops_when_configuration_changes(fake_cli, monkeypatch):
    monkeypatch.setenv(ew.ROLE_ENV, "implement=codex")
    assert ew.workers_summary()["roles"] == {"implement": "codex"}
    monkeypatch.setenv(ew.ROLE_ENV, "review=claude")
    assert ew.workers_summary()["roles"] == {"review": "claude"}


# ── 唯一的检测实现（Desktop 只读 capabilities，不再自己找） ──

def test_summary_reports_both_clis_even_before_any_role_is_configured(fake_cli):
    summary = ew.workers_summary()
    assert summary["roles"] == {} and [w["worker"] for w in summary["workers"]] == ["codex", "claude"]
    assert all(w["found"] and w["path"] for w in summary["workers"])


def test_binary_found_outside_path_like_a_gui_launch(tmp_path, monkeypatch):
    home = tmp_path / "home"
    newer, older = home / ".nvm/versions/node/v25.2.0/bin", home / ".nvm/versions/node/v20.1.0/bin"
    for directory in (newer, older):
        directory.mkdir(parents=True)
        (directory / "codex").write_text("#!/bin/sh\n")
        (directory / "codex").chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert ew.worker_binary("codex") == str(newer / "codex")
    env = ew._env_for(ew.worker_binary("codex"), {"PATH": "/usr/bin:/bin"})
    assert env["PATH"] == f"{newer}:/usr/bin:/bin"                  # 同目录的 node 才找得到


async def test_child_path_starts_with_the_cli_directory(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("FAKE_MODE", "ok")
    run = await ew.run_worker("codex", "go", str(tmp_path), write=False)
    assert run.ok                                                   # 假 CLI 的 shebang 解释器不受影响
    assert ew._env_for(ew.worker_binary("codex"), {"PATH": "/usr/bin"})["PATH"].startswith(str(fake_cli))


# ── 执行者不可用 → 内置子 Agent 接手 + 冷却 ──

def test_unavailable_signals_versus_ordinary_failures():
    assert ew.is_unavailable(ew.WorkerRun("codex", error="codex 退出码 1：You've hit your usage limit"))
    assert ew.is_unavailable(ew.WorkerRun("claude", error="Invalid API key · Please run /login"))
    assert ew.is_unavailable(ew.WorkerRun("codex", error="找不到 codex 命令"))
    assert not ew.is_unavailable(ew.WorkerRun("codex", error="codex 退出码 1：SyntaxError in generated file"))
    run = _fold("claude", [{"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}])
    assert run.rate_limited and ew.is_unavailable(run)
    assert not _fold("claude", [{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}]).rate_limited


class _Internal:
    def __init__(self):
        self.calls = []

    async def run_turn(self, message, mode="build", emit=None, **_):
        self.calls.append((message, mode))
        return "internal did it"


async def test_unavailable_worker_hands_the_block_to_the_internal_agent(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "limit")
    internal, progress = _Internal(), []
    agent = ew.ExternalWorkerAgent("codex", str(tmp_path), write=True, fallback=lambda: internal,
                                   on_progress=progress.append)
    assert await agent.run_turn("implement it") == "internal did it"
    assert internal.calls == [("implement it", "build")]
    assert ew.cooling_down("codex") and not ew.cooling_down("claude")
    assert any("不可用" in line and "内置子 Agent 接手" in line for line in progress)


async def test_ordinary_failures_still_raise_for_the_pipeline_retry(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    internal = _Internal()
    agent = ew.ExternalWorkerAgent("codex", str(tmp_path), write=True, fallback=lambda: internal)
    with pytest.raises(ew.ExternalWorkerError, match="SyntaxError"):
        await agent.run_turn("go")
    assert internal.calls == [] and not ew.cooling_down("codex")


async def test_without_fallback_unavailable_still_fails_honestly(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "limit")
    with pytest.raises(ew.ExternalWorkerError, match="usage limit"):
        await ew.ExternalWorkerAgent("codex", str(tmp_path), write=True).run_turn("go")


def test_cooling_worker_is_skipped_until_the_cooldown_ends(monkeypatch, tmp_path):
    monkeypatch.setenv(ew.ROLE_ENV, "implement=codex")
    ew.start_cooldown("codex")
    progress = []
    assert ew.external_agent_for_role("implement", str(tmp_path), on_progress=progress.append) is None
    assert progress and "冷却中" in progress[0]
    ew._COOLDOWN_UNTIL["codex"] = 0
    assert ew.external_agent_for_role("implement", str(tmp_path)) is not None
    monkeypatch.setenv(ew.COOLDOWN_ENV, "0")
    ew.start_cooldown("codex")
    assert not ew.cooling_down("codex")                            # 0 = 不冷却，每块都再试一次


async def test_dev_isolated_external_implementer_falls_back_to_the_internal_writer(fake_cli, tmp_path, monkeypatch):
    import src.agents.worktree as worktree
    from src.agents.main_agent import build_dev_tools

    monkeypatch.setenv(ew.ROLE_ENV, "implement=codex")
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "1")
    built = []

    async def fake_run_isolated_task(_repo_root, _wid, desc, build_agent, test_cmd=None, **_kw):
        work = tmp_path / "wt"
        work.mkdir(exist_ok=True)
        built.append(build_agent(str(work)))
        return "", "", None

    monkeypatch.setattr(worktree, "run_isolated_task", fake_run_isolated_task)
    tools = {t.name: t for t in build_dev_tools(str(tmp_path))}
    await tools["dev_isolated"].handler({"description": "add impl"})
    external = built[0]
    assert isinstance(external, ew.ExternalWorkerAgent) and external.fallback is not None
    internal = external.fallback()
    assert not isinstance(internal, ew.ExternalWorkerAgent) and "write_file" in internal.tools
