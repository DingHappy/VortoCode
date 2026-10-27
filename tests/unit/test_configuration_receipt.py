"""Launch receipts are non-secret, frozen, optional, and withdrawn on shutdown."""
import pytest

from src.gateway.configuration_receipt import (
    begin_configuration_receipt, configuration_receipt, end_configuration_receipt,
)


@pytest.fixture(autouse=True)
def reset_receipt(monkeypatch):
    end_configuration_receipt()
    monkeypatch.delenv("VORTOCODE_MODEL_CONFIGURATION_REVISION", raising=False)
    monkeypatch.delenv("VORTOCODE_CONFIGURATION_LAUNCH_ID", raising=False)
    yield
    end_configuration_receipt()


def test_receipt_is_frozen_and_contains_no_credentials(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MODEL_CONFIGURATION_REVISION", "revision-1")
    monkeypatch.setenv("VORTOCODE_CONFIGURATION_LAUNCH_ID", "launch-1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-test-key")
    begin_configuration_receipt()
    monkeypatch.setenv("VORTOCODE_MODEL_CONFIGURATION_REVISION", "revision-2")
    receipt = configuration_receipt()
    assert receipt == {"schema_version": 1, "revision": "revision-1", "launch_id": "launch-1"}
    receipt["revision"] = "tampered-copy"
    assert configuration_receipt()["revision"] == "revision-1"
    end_configuration_receipt()
    assert configuration_receipt() is None


@pytest.mark.parametrize("revision,launch", [("", ""), ("rev", ""), ("", "launch"),
    ("x" * 97, "launch"), ("rev\nsecret", "launch"), ("rev", "https://example.test")])
def test_missing_or_invalid_metadata_has_no_receipt(monkeypatch, revision, launch):
    monkeypatch.setenv("VORTOCODE_MODEL_CONFIGURATION_REVISION", revision)
    monkeypatch.setenv("VORTOCODE_CONFIGURATION_LAUNCH_ID", launch)
    begin_configuration_receipt()
    assert configuration_receipt() is None


@pytest.mark.asyncio
async def test_quick_health_remains_compatible_without_receipt_and_exposes_only_metadata(monkeypatch):
    from src.web.routers.ops import quick_health_check
    assert (await quick_health_check())["status"] == "healthy"
    assert (await quick_health_check())["model_configuration"] is None
    monkeypatch.setenv("VORTOCODE_MODEL_CONFIGURATION_REVISION", "rev")
    monkeypatch.setenv("VORTOCODE_CONFIGURATION_LAUNCH_ID", "launch")
    begin_configuration_receipt()
    assert (await quick_health_check())["model_configuration"] == configuration_receipt()


@pytest.mark.asyncio
async def test_lifecycle_receipt_is_ready_only_after_startup_and_removed_on_error(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.gateway.runtime import runtime_lifespan
    for name in ("VORTOCODE_IM", "VORTOCODE_SUPERVISOR_PID", "VORTOCODE_CRON", "VORTOCODE_HEARTBEAT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VORTOCODE_MODEL_CONFIGURATION_REVISION", "rev")
    monkeypatch.setenv("VORTOCODE_CONFIGURATION_LAUNCH_ID", "launch")
    async def shutdown():
        pass
    def recover():
        assert configuration_receipt() is None
        return []
    runner = SimpleNamespace(recover=recover, shutdown=shutdown)
    manager = SimpleNamespace(recover=lambda: [], shutdown=shutdown)
    terminal = SimpleNamespace(shutdown=lambda: None)
    with pytest.raises(ValueError, match="after startup"):
        async with runtime_lifespan(tmp_path, runner=runner, scheduler=None,
                audit_dependencies=lambda: {}, make_run_manager=lambda: manager,
                make_terminal_manager=lambda: terminal, register_im_worker=lambda worker: None):
            assert configuration_receipt()["revision"] == "rev"
            raise ValueError("after startup")
    assert configuration_receipt() is None
