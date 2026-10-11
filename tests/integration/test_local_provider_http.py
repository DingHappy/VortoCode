"""Real loopback HTTP + original SDK, synthetic model responses (no inference)."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from src.llm.providers import client_for_spec, reset_cache


@pytest.fixture
def local_endpoint(monkeypatch):
    requests = []
    mode = {"reject": False, "missing_usage": False, "agent_roundtrip": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
            self.respond(200, {"data": [{"id": "fixture:7b", "object": "model"}]})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": payload})
            if mode["reject"]:
                self.respond(401, {"error": {"message": "own token required", "type": "authentication_error"}})
                return
            if mode["agent_roundtrip"] and any(m.get("role") == "tool" for m in payload["messages"]):
                self.respond(200, {"id": "fixture-final", "object": "chat.completion", "created": 1,
                    "model": payload["model"], "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "read-only fixture verified"}}]})
                return
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if payload.get("tools"):
                    deltas = [
                        {"reasoning_content": "inspect"},
                        {"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":'}}]},
                        {"tool_calls": [{"index": 0, "function": {"arguments": '"README.md"}'}}]},
                    ]
                else:
                    deltas = [{"content": "local "}, {"content": "stream answer"}]
                for delta in deltas:
                    chunk = {"id": "fixture-stream", "object": "chat.completion.chunk", "created": 1,
                             "model": payload["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                end = {"id": "fixture-stream", "object": "chat.completion.chunk", "created": 1,
                       "model": payload["model"], "choices": [{"index": 0, "delta": {},
                       "finish_reason": "tool_calls" if payload.get("tools") else "stop"}]}
                self.wfile.write(("data: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
                return
            response = {"id": "fixture-completion", "object": "chat.completion", "created": 1,
                "model": payload["model"], "choices": [{"index": 0, "finish_reason": "tool_calls" if payload.get("tools") else "stop",
                    "message": {"role": "assistant", "content": None if payload.get("tools") else "local fixture answer", "tool_calls": [{"id": "call-1",
                        "type": "function", "function": {"name": "read_file", "arguments": '{"path":"README.md"}'}}]}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            if not payload.get("tools"):
                response["choices"][0]["message"].pop("tool_calls")
            if mode["missing_usage"]:
                response.pop("usage")
            self.respond(200, response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-canary-must-not-leak")
    monkeypatch.setenv("VORTOCODE_PROVIDER_LOCAL_BASE", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.delenv("VORTOCODE_PROVIDER_LOCAL_KEY", raising=False)
    reset_cache()
    try:
        yield requests, mode
    finally:
        reset_cache()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def test_local_provider_models_and_chat_over_http(local_endpoint):
    requests, _ = local_endpoint
    client = client_for_spec("local:fixture:7b")
    try:
        assert await client.list_models() == ["fixture:7b"]
        result = await client.chat([{"role": "user", "content": "inspect README"}])
        assert result["model"] == "fixture:7b"
        assert result["content"] == "local fixture answer"
        assert [r["path"] for r in requests] == ["/v1/models", "/v1/chat/completions"]
        assert all(r["auth"] == "Bearer not-required" for r in requests)
    finally:
        await client.close()


async def test_local_auth_failure_does_not_borrow_cloud_key_or_retry(local_endpoint):
    requests, mode = local_endpoint
    mode["reject"] = True
    client = client_for_spec("local:fixture:7b")
    try:
        with pytest.raises(Exception, match="401"):
            await client.chat([{"role": "user", "content": "hello"}])
        assert len(requests) == 1
        assert requests[0]["auth"] == "Bearer not-required"
    finally:
        await client.close()


async def test_local_agent_tool_schema_requires_sdk(local_endpoint):
    pytest.importorskip("openai", reason="native tool-calling acceptance requires optional llm SDK")
    requests, _ = local_endpoint
    client = client_for_spec("local:fixture:7b")
    try:
        result = await client.chat([{"role": "user", "content": "inspect README"}], tools=[{
            "type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}])
        assert result["tool_calls"] == [{"id": "call-1", "name": "read_file", "arguments": '{"path":"README.md"}'}]
        assert requests[-1]["body"]["tools"][0]["function"]["name"] == "read_file"
        assert requests[-1]["auth"] == "Bearer not-required"
    finally:
        await client.close()


async def test_local_sdk_chat_without_usage_keeps_result_without_inventing_tokens(local_endpoint):
    pytest.importorskip("openai")
    _, mode = local_endpoint
    mode["missing_usage"] = True
    client = client_for_spec("local:fixture:7b")
    try:
        result = await client.chat([{"role": "user", "content": "hello"}])
        assert result["content"] == "local fixture answer"
        assert result["usage"] == {}
    finally:
        await client.close()


async def test_local_sdk_streaming_content_and_tool_fragments(local_endpoint):
    pytest.importorskip("openai")
    requests, _ = local_endpoint
    client = client_for_spec("local:fixture:7b")
    content, reasoning = [], []
    try:
        result = await client.stream_chat([{"role": "user", "content": "hello"}], on_content=content.append)
        assert result["content"] == "local stream answer"
        assert content == ["local ", "stream answer"]
        tool_result = await client.stream_chat([{"role": "user", "content": "inspect"}], tools=[{
            "type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}],
            on_content=content.append, on_reasoning=reasoning.append)
        assert tool_result["tool_calls"] == [{"id": "call-1", "name": "read_file", "arguments": '{"path":"README.md"}'}]
        assert content == ["local ", "stream answer"]
        assert reasoning == ["inspect"]
        assert len(requests) == 2
        assert all(r["auth"] == "Bearer not-required" for r in requests)
        assert all("stream_options" not in r["body"] for r in requests)
    finally:
        await client.close()


async def test_role_file_routes_original_agent_through_read_tool_and_result(local_endpoint, tmp_path, monkeypatch):
    pytest.importorskip("openai")
    from src.agents.subagents import registry_for
    from src.agents.delegation import build_subagent
    requests, mode = local_endpoint
    mode["agent_roundtrip"] = True
    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "1")
    role_dir = tmp_path / ".vortocode" / "agents"
    role_dir.mkdir(parents=True)
    (role_dir / "local-reader.md").write_text("---\nname: local-reader\ntools: read\nmodel: local:fixture:7b\nmax_steps: 3\n---\nRead README.md and report findings.")
    original = "local-read-proof-42"
    readme = tmp_path / "README.md"
    readme.write_text(original)
    spec = registry_for(str(tmp_path)).get("local-reader")
    agent = build_subagent(str(tmp_path), spec)
    try:
        result = await agent.run_turn("Read README.md", mode="plan")
        assert result == "read-only fixture verified"
        assert len(requests) == 2
        assert all(r["body"]["model"] == "fixture:7b" for r in requests)
        assert all(r["auth"] == "Bearer not-required" for r in requests)
        tools = [m for m in requests[-1]["body"]["messages"] if m.get("role") == "tool"]
        assert len(tools) == 1
        assert original in tools[0]["content"]
        assert readme.read_text() == original
    finally:
        await agent._llm.close()


async def test_sdk_stream_interruption_closes_response_and_keeps_client_reusable(local_endpoint, monkeypatch):
    pytest.importorskip("openai")
    from src.llm.streaming import StreamInterrupted
    requests, _ = local_endpoint
    client = client_for_spec("local:fixture:7b")
    sdk = await client._get_client()
    original_create = sdk.chat.completions.create
    streams = []

    async def capture(**kwargs):
        result = await original_create(**kwargs)
        if kwargs.get("stream"):
            streams.append(result)
        return result

    monkeypatch.setattr(sdk.chat.completions, "create", capture)

    def interrupt(_):
        raise StreamInterrupted(RuntimeError("fixture stopped"))

    try:
        with pytest.raises(StreamInterrupted, match="fixture stopped"):
            await client.stream_chat([{"role": "user", "content": "hello"}], on_content=interrupt)
        assert streams[0].response.is_closed
        result = await client.chat([{"role": "user", "content": "retry explicitly"}])
        assert result["content"] == "local fixture answer"
        assert len(requests) == 2  # one interrupted call, one explicit fresh request
    finally:
        await client.close()
