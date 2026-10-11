"""Independent packaged Gateway/WS file approval probe; synthetic provider, no GUI."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import websockets

work = Path(tempfile.mkdtemp(prefix="vorto-workbench-package-"))
requested = work / "outside" / "report.txt"
requested.parent.mkdir()
requested.write_text("LOCAL-FILE-READ-VERIFIED")
workspace = work / "general"
workspace.mkdir()
requests = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = payload["messages"]
        tools = [x["function"]["name"] for x in payload.get("tools", [])]
        requests.append({"local_file_tool": "read_local_file" in tools,
                         "reasoning_effort_sent": "reasoning_effort" in payload})
        last = messages[-1]
        if last["role"] == "tool" and next((m["tool_calls"][-1]["function"]["name"] for m in reversed(messages) if m.get("tool_calls")), "") == "update_plan":
            call = {"id":"fixture-read","type":"function","function":{"name":"read_local_file","arguments":json.dumps({"path":str(requested)})}}
            delta={"tool_calls":[{"index":0,**call}]}
            message={"role":"assistant","content":None,"tool_calls":[call]}
            finish="tool_calls"
        elif last["role"] == "tool":
            content = "READ-ALLOWED" if "LOCAL-FILE-READ-VERIFIED" in str(last.get("content")) else "READ-DENIED"
            delta = {"content": content}
            message = {"role": "assistant", "content": content}
            finish = "stop"
        else:
            call = {"id": "fixture-plan", "type": "function", "function": {
                "name": "update_plan", "arguments": json.dumps({"steps":[{"step":"读取指定文件","status":"in_progress"},{"step":"交付结论","status":"pending"}]})}}
            delta = {"tool_calls": [{"index": 0, **call}]}
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
            finish = "tool_calls"
        self.send_response(200)
        if payload.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            event = {"id": "fixture", "object": "chat.completion.chunk", "model": "fixture",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode())
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "fixture", "object": "chat.completion", "model": "fixture",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}]}).encode())

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
Thread(target=server.serve_forever, daemon=True).start()
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
env = dict(os.environ)
for key in list(env):
    if key.startswith("VORTOCODE_PROVIDER_") or key in ("OPENAI_API_KEY", "OPENAI_API_BASE", "DEFAULT_MODEL", "OPENAI_MODEL", "VORTOCODE_MODEL_ROLES"):
        env.pop(key, None)
env.update(OPENAI_API_BASE=f"http://127.0.0.1:{server.server_port}/v1", OPENAI_API_KEY="fixture-nonsecret",
    DEFAULT_MODEL="fixture", OPENAI_MODEL="fixture", VORTOCODE_API_TOKEN="", AUTODEV_API_TOKEN="",
    VORTOCODE_CRON="0", VORTOCODE_HEARTBEAT="0", VORTOCODE_IM="", VORTOCODE_ENABLE_BROWSER="0",
    VORTOCODE_ENABLE_SHELL="0", VORTOCODE_WORKSPACE_SCOPE="general", VORTOCODE_DESKTOP_SIDECAR="1",
    VORTOCODE_WEB_MCP="0", VORTOCODE_MAX_STEPS="5", VORTOCODE_NATIVE_TOOLS="1", VORTOCODE_BUILD_AUTO_CONTINUES="0")
runtime = str(Path(__file__).resolve().parents[1] / "src-tauri/target/release/bundle/macos/VortoCode.app/Contents/Resources/runtime/vortocode-runtime")
log = (work / "runtime.log").open("w")
child = subprocess.Popen([runtime, "server", "--port", str(port)], cwd=workspace, env=env, stdout=log, stderr=log)
evidence = {"runtime": runtime, "work": str(work), "synthetic_model": True, "real_provider": False, "passed": False}
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=5) as response:
            return json.load(response)
    for _ in range(100):
        try:
            if api("/api/health/quick").get("status") == "healthy":
                break
        except Exception:
            time.sleep(.1)
    else:
        raise RuntimeError("health timeout")
    assert api("/api/trust")["local_file_reads"]
    cases = []
    async def run(sid, approve, expected, confirmations):
        events = []
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?sid={sid}", open_timeout=5) as ws:
            await ws.recv()
            await ws.send(json.dumps({"type": "agent", "text": f"读取 {requested}", "mode": "plan", "rid": sid}))
            for _ in range(100):
                event = json.loads(await asyncio.wait_for(ws.recv(), 25))
                events.append(event)
                Path("/tmp/vorto-workbench-probe-events.json").write_text(json.dumps(events,ensure_ascii=False,indent=2))
                if event.get("type") == "agent_confirm":
                    assert str(requested.resolve()) in event["text"]
                    cid=event["id"]
                    await ws.send(json.dumps({"type":"agent_confirm_check","id":cid,"nonce":"before"}))
                    while True:
                        check=json.loads(await asyncio.wait_for(ws.recv(),10));events.append(check)
                        if check.get("type")=="agent_confirm_state":
                            assert check["nonce"]=="before" and check["pending"] is True
                            break
                    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?sid=other-{sid}",open_timeout=5) as other:
                        await other.recv()
                        await other.send(json.dumps({"type":"agent_confirm_check","id":cid,"nonce":"other"}))
                        while True:
                            foreign=json.loads(await asyncio.wait_for(other.recv(),10))
                            if foreign.get("type")=="agent_confirm_state":
                                assert foreign["pending"] is False and foreign["nonce"]=="other"
                                break
                    await ws.send(json.dumps({"type": "agent_confirm_response", "id": cid, "ok": approve}))
                if event.get("type")=="agent_confirm_closed":
                    await ws.send(json.dumps({"type":"agent_confirm_check","id":event["id"],"nonce":"after"}))
                if event.get("type") == "agent_error":
                    raise RuntimeError(str(event))
                if event.get("type") == "agent_done":
                    if confirmations and not any(e.get("type")=="agent_confirm_state" and e.get("nonce")=="after" for e in events):
                        while True:
                            check=json.loads(await asyncio.wait_for(ws.recv(),10));events.append(check)
                            if check.get("type")=="agent_confirm_state" and check.get("nonce")=="after":
                                break
                    break
            else:
                raise RuntimeError("no completion")
        plans=[e["items"] for e in events if e.get("type")=="agent_plan" and e.get("items")]
        assert any(p[0].get("status")=="in_progress" and p[1].get("status")=="pending" for p in plans), plans
        if confirmations:
            assert any(e.get("type")=="agent_confirm_closed" for e in events)
            assert any(e.get("type")=="agent_confirm_state" and e.get("nonce")=="after" and e["pending"] is False for e in events)
        text = json.dumps(events, ensure_ascii=False)
        assert expected in text, text[-1500:]
        assert sum(e.get("type") == "agent_confirm" for e in events) == confirmations
        cases.append({"case": sid, "confirmations": confirmations, "plan_events":len(plans), "readonly_check":bool(confirmations), "expected": expected, "passed": True})
    asyncio.run(run("file-deny", False, "READ-DENIED", 1))
    asyncio.run(run("file-once", True, "READ-ALLOWED", 1))
    assert api("/api/trust")["level"] == "ask"
    status = api("/api/trust", "PUT", {"level": "reads"})
    assert status["effective"] == "reads" and status["ceiling"] == "reads"
    asyncio.run(run("file-preapproved", True, "READ-ALLOWED", 0))
    assert not any(r["reasoning_effort_sent"] for r in requests)
    audit = (workspace / ".vortocode/audit.log").read_text()
    assert "读取本机文件" in audit
    evidence.update(cases=cases, trust=status, requests=requests, audit=True, passed=True)
finally:
    child.terminate()
    try:
        child.wait(timeout=8)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    server.shutdown()
    log.close()
    Path("/tmp/vorto-workbench-package-evidence.json").write_text(json.dumps(evidence, indent=2))
print(json.dumps(evidence, indent=2))
