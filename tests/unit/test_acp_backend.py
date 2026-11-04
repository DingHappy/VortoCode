"""ACP 通用客户端（ADR-014 S2）：用一个假的 ACP 框架进程，离线验证协议、权限、文件边界、污点和超时。"""
import json
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
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
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
    monkeypatch.delenv("VORTOCODE_WORKER_HOME", raising=False)
    local = framework_env("opencode")
    assert "GH_TOKEN" not in local and "GITHUB_TOKEN" not in local
    assert local.get("HOME") == os.environ.get("HOME")
    monkeypatch.setenv("VORTOCODE_WORKER_HOME", str(tmp_path / "workers"))
    resident = framework_env("opencode")
    assert resident["HOME"] == str(tmp_path / "workers" / "opencode")
    assert resident["XDG_CONFIG_HOME"].startswith(resident["HOME"])
    assert (tmp_path / "workers" / "opencode" / ".config").is_dir()
