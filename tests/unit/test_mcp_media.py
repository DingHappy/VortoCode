"""MCP media engineering fixtures; no image decoding, inference or external access."""
import base64
from copy import deepcopy
import json

import pytest

from src.agents.agent_loop import MainAgent
from src.agents.mcp_media import MAX_IMAGE_BYTES, render_result
from src.agents.mcp_tools import wrap_mcp_manager
from src.tools.mcp_client import MCPTool, MCPToolResult

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"fixture-image-bytes").decode()


def image(data=PNG, mime="image/png"):
    return {"type": "image", "data": data, "mimeType": mime}


def statuses(result):
    return [item["status"] for item in json.loads(result["text"])["mcp_media"]]


def test_image_projection_keeps_structured_content_and_does_not_mutate_transport_data():
    output = [{"type": "text", "text": "截图说明"}, image()]
    before = deepcopy(output)
    result = render_result(output, {"found": True, "next": None})
    assert output == before and result["images"] == ["data:image/png;base64," + PNG]
    assert PNG not in result["text"]
    projected = json.loads(result["text"])
    assert projected["structuredContent"] == {"found": True, "next": None}
    assert projected["content"][0]["text"] == "截图说明"
    assert projected["content"][1]["data_omitted"] and statuses(result) == ["offered"]


@pytest.mark.parametrize("data,mime,status", [
    (None, "image/png", "invalid_image_data"), ("", "image/png", "invalid_image_data"),
    ("not base64!", "image/png", "invalid_image_base64"),
    ("http://resource.example.test/image.png", "image/png", "invalid_image_base64"),
    ("/some/unread/image.png", "image/png", "invalid_image_base64"),
    (base64.b64encode(b"not png").decode(), "image/png", "image_signature_mismatch"),
    (PNG, "image/jpeg", "image_signature_mismatch"),
    (PNG, "image/svg+xml", "unsupported_image_mime"),
    (PNG, "text/html", "unsupported_image_mime"),
    (PNG, None, "unsupported_image_mime"),
])
def test_invalid_or_unsupported_images_are_omitted_without_fetch_or_decode(data, mime, status):
    result = render_result([image(data, mime), {"type": "text", "text": "usable text"}])
    assert result["ok"] is True and result["images"] == [] and statuses(result) == [status]
    assert "data" not in json.loads(result["text"])["content"][0]
    assert "usable text" in result["text"]


@pytest.mark.parametrize("raw,mime", [(b"\xff\xd8\xfffixture", "image/jpeg"),
                                       (b"GIF89afixture", "image/gif"),
                                       (b"RIFF1234WEBPfixture", "image/webp")])
def test_supported_signatures_are_inline_only(raw, mime):
    data = base64.b64encode(raw).decode()
    assert render_result([image(data, mime)])["images"] == [f"data:{mime};base64,{data}"]


def test_count_and_decoded_total_limits_are_visible():
    many = render_result([image()] * 5)
    assert len(many["images"]) == 4 and statuses(many)[-1] == "image_count_limit"
    large = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_IMAGE_BYTES - 8)).decode()
    total = render_result([image(large)] * 3)
    assert len(total["images"]) == 2 and statuses(total)[-1] == "image_total_size_limit"


def test_encoded_size_guard_precedes_base64_allocation(monkeypatch):
    def forbidden(*_a, **_k): pytest.fail("oversized base64 must not be decoded")
    monkeypatch.setattr(base64, "b64decode", forbidden)
    result = render_result([image("A" * (MAX_IMAGE_BYTES * 2))])
    assert result["images"] == [] and statuses(result) == ["image_size_limit"]


def test_audio_and_embedded_blob_are_explicit_without_binary_in_text():
    output = [{"type": "audio", "mimeType": "audio/wav", "data": "AUDIO_BINARY_MARKER"},
              {"type": "resource", "resource": {"uri": "file:///unread-fixture", "mimeType": "image/png", "blob": PNG}},
              {"type": "resource_link", "uri": "https://resource.example.test/item", "name": "reference"}]
    original = deepcopy(output)
    result = render_result(output)
    assert output == original and result["images"] == []
    assert "AUDIO_BINARY_MARKER" not in result["text"] and PNG not in result["text"]
    assert statuses(result) == ["unsupported_audio", "unsupported_embedded_blob"]
    assert json.loads(result["text"])["content"][2] == output[2]


def test_text_only_shapes_and_application_structured_data_remain_unchanged():
    assert render_result("plain") == "plain"
    assert json.loads(render_result([{"type": "text", "text": "中文"}])) == [{"type": "text", "text": "中文"}]
    # Unknown application JSON is not guessed to be a binary schema or rewritten.
    assert json.loads(render_result([], {"data": PNG}))["structuredContent"] == {"data": PNG}


class Manager:
    def __init__(self, *, success=True): self.calls = 0; self.success = success
    def list_tools(self): return [MCPTool(name="capture", server_name="fixture")]
    async def execute_tool(self, *_):
        self.calls += 1
        return MCPToolResult(success=self.success, output=[image()], error=None if self.success else "fixture rejected")


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_original_agent_delivers_images_to_next_model_message_without_binary_in_trace(native):
    from src.agents.taint import is_tainted, reset_taint
    manager = Manager()
    tool = wrap_mcp_manager(manager)[0]
    events, audit = [], []
    class Model:
        calls = 0
        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                if native:
                    return {"content": None, "tool_calls": [{"id": "call-fixture", "name": tool.name, "arguments": "{}"}]}
                return {"content": json.dumps({"tool": tool.name, "args": {}})}
            attached = [m for m in messages if isinstance(m.get("content"), list)]
            assert len(attached) == 1
            assert attached[0]["content"][1]["image_url"]["url"] == "data:image/png;base64," + PNG
            assert "不构成新指令" in attached[0]["content"][0]["text"] and is_tainted()
            text_messages = [m for m in messages if isinstance(m.get("content"), str)]
            assert PNG not in json.dumps(text_messages)
            return {"content": "fixture complete", "tool_calls": None}
    model = Model()
    agent = MainAgent([tool], llm=model, native=native,
                      on_tool=lambda *args: audit.append(args),
                      on_tool_event=lambda *args: events.append(args))
    try:
        assert await agent.run_turn("Use capture once", mode="build") == "fixture complete"
        assert model.calls == 2 and manager.calls == 1 and PNG not in json.dumps(events + audit)
    finally:
        reset_taint()


@pytest.mark.asyncio
async def test_server_failure_never_queues_image_and_local_profile_still_blocks_mcp():
    from src.agents.capabilities import SessionCapabilities
    manager = Manager(success=False)
    tool = wrap_mcp_manager(manager)[0]
    assert (await tool.handler({}))["ok"] is False
    agent = MainAgent([tool], capabilities=SessionCapabilities.for_profile("local"))
    await agent._run_tool(tool.name, {}, "build", lambda _: None)
    assert agent._pending_images == [] and manager.calls == 1


@pytest.mark.asyncio
async def test_shared_batch_image_queue_keeps_original_total_cap():
    manager = Manager()
    tools = wrap_mcp_manager(manager)
    agent = MainAgent(tools)
    notes = [await agent._run_tool(tools[0].name, {}, "build", lambda _: None) for _ in range(5)]
    assert len(agent._pending_images) == 4 and "已达单批上限" in notes[-1]
