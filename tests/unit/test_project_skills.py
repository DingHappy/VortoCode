"""项目技能管理：两个位置、保留其它 frontmatter、版本冲突拒绝、删除只删 SKILL.md、agent 能加载到。"""
from __future__ import annotations

import pytest

from src.workflow.workbench import project_skills as ps

RICH = """---
name: code-review
description: 审查代码
version: "1.0.0"
arguments:
  - name: path
    required: true
---

审查 `$path`。
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "skills" / "code-review").mkdir(parents=True)
    (tmp_path / "skills" / "code-review" / "SKILL.md").write_text(RICH, encoding="utf-8")
    return str(tmp_path)


def test_create_local_edit_shared_and_agent_sees_both(root, tmp_path):
    created = ps.save_skill(root, "local", "release-notes",
                            {"name": "写发布说明", "description": "按 PR 汇总", "instructions": "列出本周合并的 PR", "revision": None})
    assert created["scope"] == "local" and (tmp_path / ".vortocode" / "skills" / "release-notes" / "SKILL.md").is_file()
    current = ps.get_skill(root, "shared", "code-review")
    assert current["extra_fields"] == ["arguments", "version"]
    ps.save_skill(root, "shared", "code-review", {"name": "code-review", "description": "审查代码（含安全）",
                                                   "instructions": current["instructions"] + "\n重点看注入。",
                                                   "revision": current["revision"]})
    edited = ps.get_skill(root, "shared", "code-review")
    assert edited["description"] == "审查代码（含安全）" and edited["extra_fields"] == ["arguments", "version"]
    assert "重点看注入" in edited["instructions"]

    from src.agents.tools.skills import _skill_registry_for
    names = set(_skill_registry_for(root).skills)
    assert {"写发布说明", "code-review"} <= names                       # 与 agent 加载的是同一批目录


def test_stale_revision_and_existing_id_are_rejected(root, tmp_path):
    current = ps.get_skill(root, "shared", "code-review")
    (tmp_path / "skills" / "code-review" / "SKILL.md").write_text(RICH + "\n别处的改动\n", encoding="utf-8")
    with pytest.raises(ps.SkillConflict):
        ps.save_skill(root, "shared", "code-review", {"name": "x", "instructions": "y", "revision": current["revision"]})
    with pytest.raises(ps.SkillConflict):                                # 已存在却当新建
        ps.save_skill(root, "shared", "code-review", {"name": "x", "instructions": "y", "revision": None})
    assert "别处的改动" in (tmp_path / "skills" / "code-review" / "SKILL.md").read_text()


@pytest.mark.parametrize("scope,skill_id,body,needle", [
    ("elsewhere", "ok", {"name": "a", "instructions": "b", "revision": None}, "位置"),
    ("local", "../escape", {"name": "a", "instructions": "b", "revision": None}, "技能 ID"),
    ("local", "Upper", {"name": "a", "instructions": "b", "revision": None}, "技能 ID"),
    ("local", "ok", {"name": " ", "instructions": "b", "revision": None}, "不能为空"),
    ("local", "ok", {"name": "a", "instructions": "b", "revision": None, "tools": ["Bash"]}, "不支持的字段"),
])
def test_bad_input_is_rejected(root, scope, skill_id, body, needle):
    with pytest.raises(ps.SkillError, match=needle):
        ps.save_skill(root, scope, skill_id, body)


def test_delete_only_removes_skill_md_and_needs_current_revision(root, tmp_path):
    folder = tmp_path / "skills" / "code-review"
    (folder / "check.sh").write_text("echo hi\n")
    current = ps.get_skill(root, "shared", "code-review")
    with pytest.raises(ps.SkillConflict):
        ps.delete_skill(root, "shared", "code-review", "0" * 64)
    ps.delete_skill(root, "shared", "code-review", current["revision"])
    assert not (folder / "SKILL.md").exists() and (folder / "check.sh").exists()
    assert ps.list_skills(root)["items"] == []


def test_symlinked_skill_dir_is_refused(root, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: x\n---\nhi\n")
    (tmp_path / "skills" / "linked").symlink_to(outside)
    assert any("linked" in issue for issue in ps.list_skills(root)["issues"])
    with pytest.raises(ps.SkillError, match="符号链接"):
        ps.save_skill(root, "shared", "linked", {"name": "x", "instructions": "y", "revision": None})


def test_routes(root, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.chdir(root)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.delenv("VORTOCODE_WORKSPACE_SCOPE", raising=False)
    from src.web.server import app
    client = TestClient(app)
    assert [item["id"] for item in client.get("/api/extensions/skills").json()["items"]] == ["code-review"]
    body = {"name": "n", "description": "", "instructions": "i", "revision": None}
    saved = client.put("/api/extensions/skills/local/new-one", json=body).json()
    assert client.put("/api/extensions/skills/local/new-one", json=body).status_code == 409
    assert client.get("/api/extensions/skills/local/missing").status_code == 404
    assert client.delete(f"/api/extensions/skills/local/new-one?revision={saved['revision']}").json() == {"ok": True}
