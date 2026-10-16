"""Project instruction discovery and runtime loading must share the project boundary."""
from pathlib import Path

import pytest

from src.agents.project import find_instructions_file, load_project_instructions
from src.workflow.workbench.extensions_inspect import inspect_extensions


@pytest.mark.parametrize("source", ["AGENTS.md", "CLAUDE.md", "VORTO.md", ".vortocode/AGENTS.md"])
def test_external_rule_target_is_never_read(tmp_path, monkeypatch, source):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("SYNTHETIC_OUTSIDE_RULE_MARKER")
    candidate = root / source
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.symlink_to(outside)
    original = Path.read_text
    reads = []
    def observed(path, *args, **kwargs):
        if path.resolve() == outside:
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", observed)
    assert find_instructions_file(str(root)) is None
    assert load_project_instructions(str(root)) == ""
    rule = next(item for item in inspect_extensions(str(root))["items"] if item["kind"] == "rules")
    assert rule["status"] == "blocked"
    assert not reads


def test_external_parent_directory_is_rejected(tmp_path):
    root, outside = tmp_path / "project", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "AGENTS.md").write_text("SYNTHETIC_OUTSIDE_RULE_MARKER")
    (root / ".vortocode").symlink_to(outside, target_is_directory=True)
    assert find_instructions_file(str(root)) is None
    assert load_project_instructions(str(root)) == ""


def test_blocked_high_priority_rule_falls_back_to_allowed_rule(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("SYNTHETIC_OUTSIDE_RULE_MARKER")
    (root / "AGENTS.md").symlink_to(outside)
    (root / "CLAUDE.md").write_text("SYNTHETIC_ALLOWED_RULE_MARKER")
    assert find_instructions_file(str(root)) == root / "CLAUDE.md"
    result = load_project_instructions(str(root))
    assert "SYNTHETIC_ALLOWED_RULE_MARKER" in result and "SYNTHETIC_OUTSIDE_RULE_MARKER" not in result


def test_internal_rule_link_remains_available(tmp_path):
    (tmp_path / "rules").mkdir()
    target = tmp_path / "rules/instructions.md"
    target.write_text("SYNTHETIC_ALLOWED_RULE_MARKER")
    (tmp_path / "AGENTS.md").symlink_to(target)
    assert find_instructions_file(str(tmp_path)) == tmp_path / "AGENTS.md"
    result = load_project_instructions(str(tmp_path))
    assert "AGENTS.md" in result and "SYNTHETIC_ALLOWED_RULE_MARKER" in result


@pytest.mark.parametrize("kind", ["dangling", "loop"])
def test_unresolvable_rule_falls_back_without_breaking_session(tmp_path, kind):
    (tmp_path / "AGENTS.md").symlink_to(tmp_path / ("missing.md" if kind == "dangling" else "AGENTS.md"))
    (tmp_path / "CLAUDE.md").write_text("SYNTHETIC_ALLOWED_RULE_MARKER")
    assert "SYNTHETIC_ALLOWED_RULE_MARKER" in load_project_instructions(str(tmp_path))


def test_workspace_directory_alias_uses_its_canonical_boundary(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "AGENTS.md").write_text("SYNTHETIC_ALLOWED_RULE_MARKER")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert "SYNTHETIC_ALLOWED_RULE_MARKER" in load_project_instructions(str(alias))


def test_project_session_never_injects_external_rule_into_system(tmp_path, monkeypatch):
    from src.agents import project
    from src.gateway.agent_session import build_session
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("SYNTHETIC_OUTSIDE_RULE_MARKER")
    (root / "AGENTS.md").symlink_to(outside)
    monkeypatch.setattr(project, "USER_INSTRUCTIONS_PATH", tmp_path / "missing-user.md")
    agent = build_session(str(root), kind="web", workspace_scope="project")
    assert "SYNTHETIC_OUTSIDE_RULE_MARKER" not in (agent.extra_system or "")
