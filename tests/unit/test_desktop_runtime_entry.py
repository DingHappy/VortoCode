from __future__ import annotations

import pytest

from desktop.runtime_entry import build_parser, model_capabilities


def test_desktop_runtime_entry_accepts_only_local_gateway_arguments():
    parser = build_parser()

    args = parser.parse_args(["server", "--host", "localhost", "--port", "8123"])

    assert args.command == "server"
    assert args.host == "localhost"
    assert args.port == 8123


@pytest.mark.parametrize(
    "argv",
    [
        ["server", "--host", "0.0.0.0"],
        ["server", "--port", "80"],
        ["server", "--port", "65536"],
        ["agent"],
    ],
)
def test_desktop_runtime_entry_rejects_remote_privileged_and_non_gateway_commands(argv):
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_model_capability_probe_accepts_no_endpoint_or_credentials():
    assert build_parser().parse_args(["model-capabilities"]).command == "model-capabilities"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["model-capabilities", "--base-url", "https://example.com"])


@pytest.mark.asyncio
async def test_model_capability_probe_constructs_and_closes_client_without_inference(monkeypatch):
    import importlib.metadata
    import src.llm.client as llm
    observed = []

    class FakeClient:
        def __init__(self, config):
            assert config.base_url == "http://127.0.0.1:1/v1"
            assert config.model == "diagnostic"
            assert config.api_key == ""  # placeholder is assigned by the real LLMClient constructor
        async def _get_client(self):
            observed.append("construct")
            return object()
        async def close(self):
            observed.append("close")

    monkeypatch.setattr(llm, "LLMClient", FakeClient)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "fixture-version")
    assert (await model_capabilities())["inference_sent"] is False
    assert observed == ["construct", "close"]


@pytest.mark.asyncio
async def test_model_capability_probe_fails_closed_without_sdk_and_closes_client(monkeypatch):
    import src.llm.client as llm
    closed = []

    class MissingSDK:
        def __init__(self, config):
            pass
        async def _get_client(self):
            return None
        async def close(self):
            closed.append(True)

    monkeypatch.setattr(llm, "LLMClient", MissingSDK)
    with pytest.raises(RuntimeError, match="SDK missing"):
        await model_capabilities()
    assert closed == [True]
