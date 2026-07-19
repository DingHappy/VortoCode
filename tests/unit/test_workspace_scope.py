from src.gateway.workspace_scope import (
    GENERAL,
    PROJECT,
    SCRATCH,
    current_workspace_scope,
    http_workspace_requirement,
    normalize_workspace_scope,
    workspace_scope_snapshot,
)


def test_scope_normalization_fails_closed_to_project():
    assert normalize_workspace_scope(" general ") == GENERAL
    assert normalize_workspace_scope("scratch") == SCRATCH
    assert normalize_workspace_scope("not-a-scope") == PROJECT


def test_current_scope_is_explicit_but_keeps_cli_compatibility(monkeypatch):
    monkeypatch.delenv("VORTOCODE_WORKSPACE_SCOPE", raising=False)
    assert current_workspace_scope() == PROJECT
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    assert current_workspace_scope() == GENERAL


def test_general_snapshot_does_not_expose_managed_directory(monkeypatch):
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    snapshot = workspace_scope_snapshot("/private/app-data/general")
    assert snapshot == {
        "scope": GENERAL,
        "workdir": None,
        "has_workspace": False,
        "is_project": False,
    }


def test_general_http_surface_cannot_bypass_agent_tool_boundary():
    assert http_workspace_requirement("/api/artifacts", GENERAL) is None
    assert http_workspace_requirement("/api/agent/sessions", GENERAL) is None
    assert http_workspace_requirement("/api/runtime-inbox", GENERAL) is None
    assert http_workspace_requirement("/api/git/review", GENERAL) == PROJECT
    assert http_workspace_requirement("/api/repo-memory", GENERAL) == PROJECT
    assert http_workspace_requirement("/api/extensions/inspect", GENERAL) == PROJECT
    assert http_workspace_requirement("/api/runs", GENERAL) == SCRATCH
    assert http_workspace_requirement("/api/terminals", GENERAL) == SCRATCH
    assert http_workspace_requirement("/api/tasks", GENERAL) == SCRATCH
    assert http_workspace_requirement("/api/runs", SCRATCH) is None
    assert http_workspace_requirement("/api/git/review", PROJECT) is None
