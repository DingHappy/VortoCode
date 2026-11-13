"""ACP 通用客户端（ADR-014 S2）：用一个假的 ACP 框架进程，离线验证协议、权限、文件边界、污点和超时。"""
import json
import asyncio
import os
import sys
import textwrap

import pytest

from src.agents.backends.acp import AcpSession, PermissionAsk, framework_env

FAKE = textwrap.dedent(r'''
    import json, os, sys
    SCENARIO = os.environ["FAKE_SCENARIO"]
    VERSION = int(os.environ.get("FAKE_VERSION", "1"))
    out = sys.stdout
    def send(msg):
        out.write(json.dumps(msg) + "\n"); out.flush()
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
    def update(sid, upd):
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": upd}})
    def say(sid, text):
        update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})
    OPTIONS = [{"optionId": "a1", "name": "Allow", "kind": "allow_once"},
               {"optionId": "a2", "name": "Always", "kind": "allow_always"},
               {"optionId": "r1", "name": "Reject", "kind": "reject_once"}]
    while True:
        msg = read()
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            if SCENARIO == "crash":
                sys.stderr.write("boom: missing login\n"); sys.stderr.flush(); sys.exit(3)
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": VERSION,
                  "agentCapabilities": {}, "echo": params}})
            with open(os.environ["FAKE_LOG"], "a") as log:
                log.write(json.dumps({"initialize": params}) + "\n")
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "s1"}})
        elif method == "session/cancel":
            with open(os.environ["FAKE_LOG"], "a") as log:
                log.write(json.dumps({"cancel": params}) + "\n")
        elif method == "session/prompt" and SCENARIO == "silent":
            PROMPTS = globals().get("PROMPTS", 0) + 1
            globals()["PROMPTS"] = PROMPTS
            if PROMPTS > 1:
                say(params["sessionId"], "第二次才交回：" + params["prompt"][0]["text"][:6])
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        elif method == "session/prompt":
            sid = params["sessionId"]
            print("not json, framework log line")
            say(sid, "收到：" + params["prompt"][0]["text"] + "。")
            if SCENARIO in ("permission", "network"):
                kind = "fetch" if SCENARIO == "network" else "execute"
                update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "run tests", "kind": kind, "status": "pending"})
                reply = call("session/request_permission", {"sessionId": sid, "options": OPTIONS,
                             "toolCall": {"toolCallId": "t1", "title": "pytest -q", "kind": kind, "rawInput": {"command": "pytest -q"}}}, 900)
                say(sid, "选择=" + json.dumps(reply.get("result")))
            if SCENARIO == "local":
                update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "read a.py", "kind": "read", "status": "completed"})
            if SCENARIO == "foreign":
                say("another-session", "foreign result must not enter this execution")
            if SCENARIO == "files":
                inside = call("fs/read_text_file", {"sessionId": sid, "path": os.path.join(os.environ["FAKE_ROOT"], "a.txt"), "line": 2, "limit": 1}, 901)
                outside = call("fs/read_text_file", {"sessionId": sid, "path": "/etc/hosts"}, 902)
                wrote = call("fs/write_text_file", {"sessionId": sid, "path": os.path.join(os.environ["FAKE_ROOT"], "out.txt"), "content": "hi"}, 903)
                unknown = call("terminal/create", {"sessionId": sid, "command": "ls"}, 904)
                say(sid, json.dumps({"inside": inside.get("result"), "outside": "error" in outside,
                                     "wrote": "error" not in wrote, "unknown": unknown.get("error", {}).get("code")}))
            if SCENARIO == "stubborn":             # 不理 cancel、也不读输入：只能被结束进程组
                import time
                time.sleep(120)
            if SCENARIO == "hang":                 # 一直不结束，直到收到 session/cancel
                while True:
                    nxt = read()
                    if nxt.get("method") == "session/cancel":
                        with open(os.environ["FAKE_LOG"], "a") as log:
                            log.write(json.dumps({"cancel": nxt.get("params")}) + "\n")
                        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
                        break
                continue
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "future_stop" if SCENARIO == "unknown_stop" else "end_turn"}})
''')


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "fake_acp.py"
    script.write_text(FAKE)
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.txt").write_text("one\ntwo\nthree\n")
    log = tmp_path / "log.jsonl"
    log.write_text("")

    def make(scenario, *, decide=None, version=1, **kwargs):
        env = {**os.environ, "FAKE_SCENARIO": scenario, "FAKE_VERSION": str(version),
               "FAKE_LOG": str(log), "FAKE_ROOT": str(root)}
        extra = {"decide": decide} if decide is not None else {}
        return AcpSession([sys.executable, str(script)], cwd=str(root), env=env, **extra, **kwargs)

    make.root, make.log = root, log
    return make


def _log(fake):
    return [json.loads(line) for line in fake.log.read_text().splitlines() if line.strip()]


async def test_execution_report_tracks_bound_session_and_publishes_terminal_after_cleanup(fake):
    from src.agents.backends.execution import ExecutionRef, ExecutionSnapshot, ExecutionState
    reports = []
    owned = ExecutionRef('opencode', 'fixture-instance', 'attempt', task_id='task-1')
    def observe(report):
        reports.append((report, session._proc.returncode if session._proc else None))
    session = fake('local', execution_ref=owned, on_snapshot=observe)
    result = await session.run('inspect', timeout=20)
    assert result.text == '收到：inspect。'
    report = session.execution_report
    assert reports[0][0].state == ExecutionState.QUEUED
    assert any(r.state == ExecutionState.RUNNING and r.progress.summary for r, _ in reports)
    assert reports[-1][1] is not None
    assert report.state == ExecutionState.FINISHED
    assert report.ref.key == owned.key and report.ref.task_id == 'task-1'
    assert report.ref.session_id == 's1'
    assert report.result.summary == result.text and not report.result.evidence
    assert report.result.usage.cost_usd is None
    assert ExecutionSnapshot.from_dict(report.to_dict()) == report


@pytest.mark.parametrize('scenario,code', [('silent', 'empty_result'), ('unknown_stop', 'unknown_stop_reason')])
async def test_execution_report_does_not_invent_success_from_empty_or_unknown_stop(fake, scenario, code):
    from src.agents.backends.execution import ExecutionState
    session = fake(scenario)
    await session.run('inspect', timeout=20)
    assert session.execution_report.state == ExecutionState.UNKNOWN
    assert session.execution_report.error.code == code
    assert session.execution_report.result is None


async def test_execution_report_observer_failure_does_not_change_protocol_or_permissions(fake):
    from src.agents.backends.execution import ExecutionState
    def broken(report):
        raise OSError('fixture observer offline')
    session = fake('permission', on_snapshot=broken)
    result = await session.run('inspect', timeout=20)
    assert result.permissions[0]['allowed'] is False and result.error == ''
    assert session.execution_report.state == ExecutionState.FINISHED


async def test_execution_report_cancellation_is_observed_only_after_process_cleanup(fake, monkeypatch):
    from src.agents.backends.execution import ExecutionState
    session = fake('plain')
    started = asyncio.Event()
    async def pending(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(session, '_request', pending)
    run = asyncio.create_task(session.run('inspect', timeout=20))
    await started.wait()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert session._proc.returncode is not None
    assert session.execution_report.state == ExecutionState.CANCELLED


async def test_execution_report_missing_program_has_not_started_effect(tmp_path):
    from src.agents.backends.execution import ExecutionState
    session = AcpSession([str(tmp_path / 'missing')], cwd=str(tmp_path))
    await session.run('inspect', timeout=20)
    assert session.execution_report.state == ExecutionState.FAILED
    assert session.execution_report.error.effect == 'not_started'


async def test_execution_report_ignores_foreign_session_updates_and_rejects_reuse(fake):
    session = fake('foreign')
    result = await session.run('inspect', timeout=20)
    assert result.text == session.execution_report.result.summary == '收到：inspect。'
    with pytest.raises(RuntimeError, match='once'):
        await session.run('again', timeout=20)


async def test_execution_report_cleanup_failure_remains_unknown(fake, monkeypatch):
    from src.agents.backends.execution import ExecutionState
    session = fake('plain')
    close = session._close
    async def unconfirmed():
        await close()
        raise OSError('fixture cleanup receipt unavailable')
    monkeypatch.setattr(session, '_close', unconfirmed)
    with pytest.raises(OSError):
        await session.run('inspect', timeout=20)
    assert session.execution_report.state == ExecutionState.UNKNOWN
    assert session.execution_report.error.code == 'cleanup_unconfirmed'


async def test_happy_path_collects_text_and_ignores_stdout_noise(fake):
    result = await fake("plain").run("你好", timeout=20)
    assert result.error == ""
    assert result.stop_reason == "end_turn"
    assert result.text == "收到：你好。"
    assert result.networked is False
    caps = _log(fake)[0]["initialize"]["clientCapabilities"]
    assert caps == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}


async def test_allowed_permission_selects_allow_once_never_always(fake):
    asks = []

    def decide(ask: PermissionAsk) -> bool:
        asks.append(ask)
        return True

    result = await fake("permission", decide=decide).run("跑测试", timeout=20)
    assert '"optionId": "a1"' in result.text
    assert asks[0].kind == "execute" and asks[0].raw_input == {"command": "pytest -q"}
    assert result.permissions == [{"title": "pytest -q", "kind": "execute", "allowed": True}]
    assert result.networked is True                     # 命令也能出网：按可能联网记


async def test_denied_or_failing_decision_rejects(fake):
    async def boom(_ask):
        raise RuntimeError("gate down")

    for decide in (lambda _ask: False, boom, lambda _ask: "yes"):
        result = await fake("permission", decide=decide).run("跑测试", timeout=20)
        assert '"optionId": "r1"' in result.text
        assert result.permissions[0]["allowed"] is False


async def test_default_decision_is_deny(fake):
    result = await fake("permission").run("跑测试", timeout=20)
    assert '"optionId": "r1"' in result.text


async def test_network_tool_marks_networked_and_local_tool_does_not(fake):
    assert (await fake("network", decide=lambda _ask: False).run("查", timeout=20)).networked is True
    local = await fake("local").run("读", timeout=20)
    assert local.networked is False
    assert local.tool_calls == [{"id": "t2", "title": "read a.py", "kind": "read", "status": "completed"}]


async def test_files_stay_inside_roots_and_writes_need_permission(fake):
    allowed = []
    session = fake("files", decide=lambda ask: allowed.append(ask) or True,
                   read_root=str(fake.root), write_root=str(fake.root))
    result = await session.run("读写", timeout=20)
    report = json.loads(result.text.split("。", 1)[1])
    assert report == {"inside": {"content": "two\n"}, "outside": True, "wrote": True, "unknown": -32601}
    assert (fake.root / "out.txt").read_text() == "hi"
    assert allowed[0].kind == "edit"
    caps = _log(fake)[0]["initialize"]["clientCapabilities"]["fs"]
    assert caps == {"readTextFile": True, "writeTextFile": True}


async def test_write_refused_without_permission(fake):
    session = fake("files", decide=lambda _ask: False, read_root=str(fake.root), write_root=str(fake.root))
    result = await session.run("读写", timeout=20)
    assert json.loads(result.text.split("。", 1)[1])["wrote"] is False
    assert not (fake.root / "out.txt").exists()


async def test_timeout_sends_cancel_and_ends_process(fake, monkeypatch):
    monkeypatch.setattr("src.agents.backends.acp._CANCEL_GRACE", 0.2)
    session = fake("hang")
    result = await session.run("卡住", timeout=1.5)
    assert result.error == "超过单轮时限"
    assert {"cancel": {"sessionId": "s1"}} in _log(fake)
    assert session._proc is not None and session._proc.returncode is not None


async def test_framework_ignoring_cancel_is_killed(fake, monkeypatch):
    monkeypatch.setattr("src.agents.backends.acp._CANCEL_GRACE", 0.1)
    session = fake("stubborn")
    result = await session.run("卡住", timeout=1)
    assert result.error == "超过单轮时限"
    assert session._proc is not None and session._proc.returncode is not None


async def test_crash_reports_stderr_tail(fake):
    result = await fake("crash").run("hi", timeout=20)
    assert "提前退出" in result.error and "missing login" in result.error


async def test_protocol_version_mismatch_is_refused(fake):
    result = await fake("plain", version=2).run("hi", timeout=20)
    assert "ACP 版本" in result.error
    assert result.text == ""


async def test_missing_binary_says_not_installed(tmp_path):
    result = await AcpSession(["definitely-not-a-real-acp-cli"], cwd=str(tmp_path), env={}).run("hi", timeout=5)
    assert "还没装" in result.error


def test_framework_env_strips_tokens_and_isolates_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.delenv("VORTOONE_WORKER_HOME", raising=False)
    local = framework_env("opencode")
    assert "GH_TOKEN" not in local and "GITHUB_TOKEN" not in local
    assert local.get("HOME") == os.environ.get("HOME")
    monkeypatch.setenv("VORTOONE_WORKER_HOME", str(tmp_path / "workers"))
    resident = framework_env("opencode")
    assert resident["HOME"] == str(tmp_path / "workers" / "opencode")
    assert resident["XDG_CONFIG_HOME"].startswith(resident["HOME"])
    assert (tmp_path / "workers" / "opencode" / ".config").is_dir()


async def test_a_silent_turn_gets_one_followup_in_the_same_session(fake):
    session = fake("silent")
    result = await session.run("查一下", timeout=20, followup="你没交回内容")
    assert result.text == "第二次才交回：你没交回内容" and result.followed_up is True
    plain = await fake("silent").run("查一下", timeout=20)              # 不给 followup 就不追问
    assert plain.text == "" and plain.followed_up is False
    answered = await fake("plain").run("你好", timeout=20, followup="不该发")
    assert answered.followed_up is False and answered.text == "收到：你好。"
