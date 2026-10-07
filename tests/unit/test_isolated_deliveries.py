"""Conversational isolated delivery remains reviewable outside the active worktree."""
import subprocess

import pytest

from src.gateway.isolated_deliveries import (
    isolated_delivery_diff,
    isolated_delivery_snapshot,
    list_isolated_deliveries,
    record_isolated_delivery,
)


def _git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_isolated_delivery_records_exact_branch_and_invalidates_old_verification(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-qb", "vorto/example")
    (tmp_path / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "fix")
    record = record_isolated_delivery(
        str(tmp_path), branch="vorto/example", description="Fix app", base_oid=base,
        verification={"ok": True, "cmd": "pytest -q", "output": "1 passed\nAPI_KEY=example-secret"}, attempts=2)

    delivery = isolated_delivery_snapshot(str(tmp_path), record["id"])
    assert delivery["unchanged"] is True
    assert "example-secret" not in delivery["verification"]["output"]
    assert "1 passed" in delivery["verification"]["output"]
    assert [item["path"] for item in delivery["files"]] == ["app.py"]
    assert "+value = 2" in isolated_delivery_diff(str(tmp_path), record["id"], "app.py")["diff"]
    assert len(list_isolated_deliveries(str(tmp_path))) == 1
    with pytest.raises(ValueError):
        isolated_delivery_diff(str(tmp_path), record["id"], "../secret")

    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from src.web.server import app

    client = TestClient(app)
    assert client.get("/api/git/isolated-deliveries").json()["deliveries"][0]["id"] == record["id"]
    assert client.get(f"/api/git/isolated-deliveries/{record['id']}").json()["files"][0]["path"] == "app.py"
    response = client.get(f"/api/git/isolated-deliveries/{record['id']}/diff", params={"path": "app.py"})
    assert response.status_code == 200 and "+value = 2" in response.json()["diff"]

    (tmp_path / "app.py").write_text("value = 3\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "later")
    assert isolated_delivery_snapshot(str(tmp_path), record["id"])["unchanged"] is False
