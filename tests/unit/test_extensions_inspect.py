"""Desktop extension inventory is effective, bounded, redacted, and non-executing."""
from __future__ import annotations

import json


def _skill(path, name: str, description: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\ncapabilities: [read]\n---\nDo the work.\n",
        encoding="utf-8",
    )


def test_inspect_extensions_reports_effective_sources_without_sensitive_bodies(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "AGENTS.md").write_text("Never expose this rule body", encoding="utf-8")
    (root / "CLAUDE.md").write_text("Shadowed rule body", encoding="utf-8")
    _skill(root / "skills" / "review" / "SKILL.md", "review", "Review safely")
    _skill(root / ".vortocode" / "skills" / "review" / "SKILL.md", "review", "Project override")

    (root / ".vortocode" / "hooks.yaml").write_text(
        "hooks:\n  - name: notify\n    type: http\n    event_types: [agent_end]\n"
        "    url: https://example.test/done?token=do-not-return\n",
        encoding="utf-8",
    )
    (root / "config").mkdir()
    (root / "config" / "mcp.yaml").write_text(
        "servers:\n"
        "  - name: local-files\n    transport: stdio\n    command: secret-command --token topsecret\n"
        "  - name: safe-http\n    transport: http\n    credentialed: false\n"
        "    url: https://mcp.example.test/v1\n"
        "  - name: credentialed\n    transport: http\n    headers: {Authorization: Bearer topsecret}\n"
        "    url: https://mcp.example.test/private\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VORTOCODE_HOOK_TRUST_STORE", str(tmp_path / "trust.json"))

    from src.gateway.extensions_inspect import inspect_extensions

    snapshot = inspect_extensions(str(root))
    encoded = json.dumps(snapshot, ensure_ascii=False)
    assert snapshot["version"] == 1
    assert snapshot["summary"]["attention"] >= 3
    assert "Never expose this rule body" not in encoded
    assert "Shadowed rule body" not in encoded
    assert "secret-command" not in encoded
    assert "topsecret" not in encoded
    assert "do-not-return" not in encoded
    assert "https://mcp.example.test" not in encoded

    rules = [item for item in snapshot["items"] if item["kind"] == "rules"]
    assert next(item for item in rules if item["source"] == "AGENTS.md")["status"] == "active"
    shadowed_rule = next(item for item in rules if item["source"] == "CLAUDE.md")
    assert shadowed_rule["status"] == "available" and shadowed_rule["enabled"] is False
    skills = [item for item in snapshot["items"] if item["kind"] == "skill"]
    assert next(item for item in skills if item["source"].startswith(".vortocode/"))["status"] == "active"
    shadowed_skill = next(item for item in skills if item["source"].startswith("skills/"))
    assert shadowed_skill["status"] == "available" and shadowed_skill["enabled"] is False
    mcp = {item["name"]: item for item in snapshot["items"] if item["kind"] == "mcp"}
    assert mcp["safe-http"]["status"] == "available"
    assert mcp["local-files"]["status"] == "blocked"
    assert mcp["credentialed"]["status"] == "blocked"


def test_extensions_inspect_route_uses_project_boundary(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    from fastapi.testclient import TestClient
    from src.web.server import app

    response = TestClient(app).get("/api/extensions/inspect")
    assert response.status_code == 200
    assert response.json()["version"] == 1
