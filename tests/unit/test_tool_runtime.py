"""Independent tool-host contracts; no model/network or MainAgent is required."""
from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from src.agents.capabilities import HOST_PROCESS, SessionCapabilities
from src.agents.gate import make_confirm_gate
from src.agents.permissions import Permissions
from src.agents.taint import is_tainted, reset_taint
from src.agents.tool import Tool, ToolTurnYield, function_schemas, tool_definitions
from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime


@pytest.fixture(autouse=True)
def clean_taint():
    reset_taint()
    yield
    reset_taint()


def host(tools, **options):
    registry = {tool.name: tool for tool in tools}
    return ToolRuntime(lambda: registry, ToolExecutionContext(**options)), registry


async def echo(args):
    return args.get("text", "ok")


def test_runtime_can_load_without_a_model_loop_or_transport():
    code = (
        "import sys; from src.agents.tool_runtime import ToolRuntime; "
        "assert 'src.agents.agent_loop' not in sys.modules; "
        "assert 'src.agents.main_agent' not in sys.modules; "
        "assert not any(m.startswith(('src.gateway', 'src.web', 'src.workflow')) for m in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_schema_adapters_preserve_types_required_fields_and_private_policy():
    parameters = {"type": "object", "properties": {"count": {"type": "integer"},
        "items": {"type": "array", "items": {"type": "boolean"}}},
        "required": ["count"], "additionalProperties": False}
    tool = Tool("inspect", "Inspect", {}, echo, input_schema=parameters,
                required_capabilities=(HOST_PROCESS,), read_only=False)
    declaration = tool_definitions([tool])[0]
    wire = function_schemas([tool])[0]
    assert wire["function"]["parameters"] == declaration["input_schema"] == parameters
    assert set(declaration) == {"name", "description", "input_schema"}
    wire["function"]["parameters"]["required"].clear()
    declaration["input_schema"]["properties"]["items"]["items"]["type"] = "string"
    assert parameters["required"] == ["count"]
    assert parameters["properties"]["items"]["items"]["type"] == "boolean"


def test_legacy_schema_export_is_also_a_deep_copy():
    tool = Tool("legacy", "Old contract", {"limit": "Maximum"}, echo,
                argument_schema={"limit": {"type": "integer", "enum": [1, 2]}})
    exported = tool_definitions([tool])[0]["input_schema"]
    assert exported["required"] == []
    assert exported["properties"]["limit"]["description"] == "Maximum"
    exported["properties"]["limit"]["enum"].append(3)
    assert tool.argument_schema["limit"]["enum"] == [1, 2]


async def test_foreign_core_can_execute_schema_calls_without_constructing_main_agent():
    runtime, registry = host([Tool("echo", "Echo", {"text": "Text"}, echo)])
    # A foreign core owns model messages; only tool name/arguments enter the host.
    call = {"name": runtime.definitions()[0]["name"], "arguments": {"text": "hello"}}
    outcome = await runtime.execute(call["name"], call["arguments"])
    assert (outcome.status, outcome.text) == ("succeeded", "hello")
    assert outcome.call_id.startswith("tool-")
    registry.clear()
    missing = await runtime.execute("echo", {})
    assert missing.status == "blocked"


@pytest.mark.parametrize("mode", ["plan", "unknown"])
async def test_writes_require_explicit_mode_and_escalation(mode):
    ran = []
    async def write(args):
        ran.append(args)
        return "written"
    runtime, _ = host([Tool("write", "Write", {}, write, read_only=False)])
    assert (await runtime.execute("write", {}, mode)).status == "blocked"
    assert ran == []


async def test_mode_escalation_is_shared_with_the_callers_turn_state():
    asked = []
    async def approve(name, args):
        asked.append(name)
        return True
    runtime, _ = host([Tool("write", "Write", {}, echo, read_only=False)], on_escalate=approve)
    assert (await runtime.execute("write", {})).status == "succeeded"
    assert (await runtime.execute("write", {})).status == "succeeded"
    assert runtime.context.state.escalated and asked == ["write"]
    other, _ = host([Tool("write", "Write", {}, echo, read_only=False)])
    assert (await other.execute("write", {})).status == "blocked"


@pytest.mark.parametrize("restriction", ["capability", "permission", "hook"])
async def test_guards_block_before_handlers_or_escalation(restriction):
    calls = []
    async def handler(args):
        calls.append("handler")
        return "bad"
    async def approve(name, args):
        calls.append("approve")
        return True
    async def hook(stage, payload, stoppable=False):
        return "blocked by hook" if stoppable else None
    options = {"on_escalate": approve}
    required = (HOST_PROCESS,) if restriction == "capability" else ()
    if restriction == "permission":
        options["permissions"] = Permissions(deny=[("protected", None)])
    if restriction == "hook":
        options["on_hook"] = hook
    runtime, _ = host([Tool("protected", "Protected", {}, handler,
        read_only=restriction == "hook", required_capabilities=required)], **options)
    outcome = await runtime.execute("protected", {})
    assert outcome.status == "blocked" and calls == []


async def test_single_external_call_taints_subsequent_shared_confirmation_gate():
    gate = make_confirm_gate(auto_approve=True)
    ran = []
    async def protected(args):
        if not await gate("write"):
            return {"ok": False, "text": "denied"}
        ran.append(True)
        return "written"
    runtime, _ = host([Tool("external", "External data", {}, echo, untrusted_source=True),
        Tool("protected", "Write", {}, protected, read_only=False)])
    await runtime.execute("external", {})
    assert is_tainted()
    outcome = await runtime.execute("protected", {}, "build")
    assert outcome.status == "failed" and outcome.text == "denied" and ran == []


async def test_separate_request_tasks_retain_the_same_turns_taint_before_confirmation():
    gate = make_confirm_gate(auto_approve=True)
    async def protected(args):
        approved = await gate("write")
        return {"ok": approved, "text": "written" if approved else "denied"}
    runtime, _ = host([Tool("external", "Read", {}, echo, untrusted_source=True),
        Tool("protected", "Write", {}, protected, read_only=False)])
    # RPC adapters may service each call in a separate asyncio task. A child's
    # ContextVar never flows back to the request dispatcher, but turn state does.
    await asyncio.create_task(runtime.execute("external", {}))
    assert not is_tainted()
    outcome = await asyncio.create_task(runtime.execute("protected", {}, "build"))
    assert outcome.status == "failed" and outcome.text == "denied"


async def test_parallel_external_reads_propagate_taint_to_parent_turn():
    ready = asyncio.Event()
    entered = []
    async def read(args):
        entered.append(args["id"])
        if len(entered) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=1)
        return str(args["id"])
    runtime, _ = host([Tool("external", "Read", {}, read, untrusted_source=True)])
    result = await runtime.execute_batch([("external", {"id": 1}), ("external", {"id": 2})])
    assert [outcome.text for _, outcome in result] == ["1", "2"]
    assert is_tainted()


async def test_revoking_an_external_tool_during_parallel_reads_does_not_erase_taint():
    entered = 0
    ready = asyncio.Event()
    async def read(args):
        nonlocal entered
        entered += 1
        if entered == 2:
            ready.set()
        await ready.wait()
        registry.pop("external", None)
        return "already ingested external data"
    runtime, registry = host([Tool("external", "Read", {}, read, untrusted_source=True)])
    await runtime.execute_batch([("external", {}), ("external", {})])
    assert "external" not in registry and is_tainted()


async def test_serial_reads_never_overlap_confirmation_callbacks():
    running = peak = 0
    async def read(args):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        running -= 1
        return "read"
    runtime, _ = host([Tool("read", "Approval may be needed", {}, read, serial=True)])
    await runtime.execute_batch([("read", {}), ("read", {})])
    assert peak == 1


async def test_yield_stops_the_rest_of_a_batch():
    ran = []
    async def suspend(args):
        raise ToolTurnYield("durably suspended")
    async def later(args):
        ran.append(True)
        return "unexpected"
    runtime, _ = host([Tool("suspend", "Suspend", {}, suspend, yields_turn=True),
                       Tool("later", "Later", {}, later)])
    with pytest.raises(ToolTurnYield, match="durably suspended"):
        await runtime.execute_batch([("suspend", {}), ("later", {})])
    assert ran == []


@pytest.mark.parametrize("batch", [False, True])
async def test_cancellation_is_not_converted_into_success_or_failure_text(batch):
    events = []
    async def cancelled(args):
        raise asyncio.CancelledError()
    runtime, _ = host([Tool("cancel", "Cancel", {}, cancelled)],
        on_event=lambda stage, payload: events.append((stage, payload)))
    with pytest.raises(asyncio.CancelledError):
        if batch:
            await runtime.execute_batch([("cancel", {}), ("cancel", {})])
        else:
            await runtime.execute("cancel", {})
    assert any(stage == "finish" and payload["status"] == "cancelled" for stage, payload in events)


async def test_explicit_failures_and_observer_errors_do_not_become_successes():
    async def failed(args):
        return {"ok": False, "text": "test failed"}
    def broken_observer(*args):
        raise RuntimeError("UI disconnected")
    runtime, _ = host([Tool("test", "Test", {}, failed)],
                      on_event=broken_observer, on_result=broken_observer)
    outcome = await runtime.execute("test", {})
    assert outcome.status == "failed" and outcome.text == "test failed"


async def test_a_core_without_media_support_cannot_silently_discard_images():
    async def image(args):
        return {"text": "screenshot", "images": ["/tmp/example.png"]}
    runtime, _ = host([Tool("image", "Image", {}, image)])
    outcome = await runtime.execute("image", {})
    assert outcome.status == "failed" and "图片结果处理" in outcome.text and is_tainted()


async def test_execution_revocation_is_checked_before_the_handler():
    ran = []
    async def handler(args):
        ran.append(True)
        return "bad"
    def revoked():
        raise RuntimeError("execution revoked")
    runtime, _ = host([Tool("run", "Run", {}, handler)], check_execution=revoked)
    with pytest.raises(RuntimeError, match="execution revoked"):
        await runtime.execute("run", {})
    assert ran == []


async def test_supervised_sensitive_file_denial_survives_a_local_profile(tmp_path):
    runtime, _ = host([Tool("read_file", "Read", {"path": "Path"}, echo)],
        supervised=True, capabilities=SessionCapabilities.for_profile("local", str(tmp_path)))
    outcome = await runtime.execute("read_file", {"path": ".env"})
    assert outcome.status == "blocked" and "监管拦截" in outcome.text
