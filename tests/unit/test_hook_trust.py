"""Project hook trust is user-owned, explicit, reloadable, and non-executing by default."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".vortocode").mkdir()
    (root / ".vortocode" / "hooks.yaml").write_text(
        "hooks:\n"
        "  - name: format\n"
        "    type: command\n"
        "    event_types: [post_tool_use]\n"
        "    matcher: write_file|edit_file\n"
        "    command: ruff format .\n"
        "  - name: notify\n"
        "    type: http\n"
        "    event_types: [agent_end]\n"
        "    url: https://hooks.example.test/done?token=secret\n",
        encoding="utf-8",
    )
    return root


def test_project_cannot_commit_its_own_hook_trust(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    store = tmp_path / "user-owned" / "trusted.json"
    monkeypatch.setenv("VORTOCODE_HOOK_TRUST_STORE", str(store))
    from src.hooks.trust import hook_config_status, is_project_trusted, set_project_trusted

    assert not is_project_trusted(str(root))
    preview = hook_config_status(str(root))
    assert preview["configured"] and not preview["active"] and len(preview["hooks"]) == 2
    assert "token=secret" not in json.dumps(preview)  # URL query credentials never reach Desktop review UI
    assert preview["hooks"][0]["capabilities"] == [
        "emit_annotation", "observe_event", "run_command",
    ]
    assert preview["hooks"][1]["capabilities"] == [
        "emit_annotation", "observe_event", "send_http",
    ]
    assert set_project_trusted(str(root), True)
    assert is_project_trusted(str(root)) and store.is_file()
    assert not (root / ".vortocode" / "trusted-folders.json").exists()
    assert set_project_trusted(str(root), False)
    assert not is_project_trusted(str(root))


def test_agent_session_loads_project_hooks_only_after_trust(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setenv("VORTOCODE_HOOK_TRUST_STORE", str(tmp_path / "trust.json"))
    from src.gateway.agent_session import load_project_hook_system
    from src.hooks.trust import set_project_trusted

    assert load_project_hook_system(str(root)) is None
    preview = load_project_hook_system(str(root), require_trust=False)
    assert preview is not None and preview.registry.get("format") is not None
    assert set_project_trusted(str(root), True)
    active = load_project_hook_system(str(root))
    assert active is not None and active.registry.get("notify") is not None


def test_hook_preview_normalizes_single_event_and_invalid_timeout(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setenv("VORTOCODE_HOOK_TRUST_STORE", str(tmp_path / "trust.json"))
    (root / ".vortocode" / "hooks.yaml").write_text(
        "hooks:\n  - name: one\n    event_types: post_tool_use\n"
        "    command: true\n    timeout: not-a-number\n",
        encoding="utf-8",
    )
    from src.hooks.trust import hook_config_status

    preview = hook_config_status(str(root))
    assert preview["error"] == ""
    assert preview["hooks"][0]["events"] == ["post_tool_use"]
    assert preview["hooks"][0]["timeout"] == 5


def test_hook_trust_api_hot_reloads_live_agents(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setenv("VORTOCODE_HOOK_TRUST_STORE", str(tmp_path / "trust.json"))
    from src.web.routers import realtime
    from src.web.server import app

    class Agent:
        _hook_system = None

    realtime._SESSIONS["sid-hook-test"] = {"agent": Agent(), "last": 0.0}
    try:
        client = TestClient(app)
        before = client.get("/api/hooks")
        assert before.status_code == 200 and before.json()["trusted"] is False
        enabled = client.put("/api/hooks/trust", json={"trusted": True})
        assert enabled.status_code == 200 and enabled.json()["active"] is True
        assert enabled.json()["sessions_reloaded"] >= 1
        assert realtime._SESSIONS["sid-hook-test"]["agent"]._hook_system is not None
        disabled = client.put("/api/hooks/trust", json={"trusted": False})
        assert disabled.status_code == 200 and disabled.json()["active"] is False
        assert realtime._SESSIONS["sid-hook-test"]["agent"]._hook_system is None
    finally:
        realtime._SESSIONS.pop("sid-hook-test", None)
