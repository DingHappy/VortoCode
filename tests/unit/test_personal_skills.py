"""Personal instruction approval, source boundaries and real General assembly."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.gateway.agent_session import build_session
from src.workflow.workbench.personal_skills import PersonalSkills, SkillConflict


def draft(**changes):
    return {"name": "示例流程", "description": "比较三个方案", "instructions": "列出方案并核对来源。", "revision": None, **changes}


def approved(root):
    service = PersonalSkills(str(root))
    item = service.save("compare", draft())
    return service, service.set_enabled("compare", enabled=True, revision=item["revision"])


def test_save_preview_and_version_approval_are_durable_and_private(tmp_path):
    skills = PersonalSkills(str(tmp_path))
    assert skills.list() == {"items": [], "issues": [], "truncated": False}
    assert not skills.base.exists()
    saved = skills.save("compare", draft())
    assert saved["status"] == "needs_review" and not saved["enabled"]
    assert "instructions" not in saved and str(tmp_path) not in json.dumps(saved)
    assert "instructions" not in skills.list()["items"][0]
    preview = skills.get("compare", preview=True)
    assert preview["instructions"] == draft()["instructions"]
    assert "personal-skills/" in (tmp_path / ".vortocode/.gitignore").read_text()
    for name in ("SKILL.md", "approval.json"):
        assert (skills.base / "compare" / name).stat().st_mode & 0o777 == 0o600
    skills.set_enabled("compare", enabled=True, revision=preview["revision"])
    reopened = PersonalSkills(str(tmp_path))
    assert reopened.load_instructions("compare")["instructions"] == preview["instructions"]


def test_disable_and_file_changes_reject_existing_agent_tool(tmp_path):
    skills, item = approved(tmp_path)
    tools = build_session(str(tmp_path), kind="web", workspace_scope="general").tools
    assert "use_personal_skill" in tools
    skills.set_enabled("compare", enabled=False, revision=item["revision"])
    with pytest.raises(ValueError): skills.load_instructions("compare")
    skills.set_enabled("compare", enabled=True, revision=item["revision"])
    path = skills.base / "compare/SKILL.md"
    path.write_text(path.read_text() + "\n新增指令。")
    assert not skills.get("compare")["enabled"]
    with pytest.raises(ValueError): skills.load_instructions("compare")
    with pytest.raises(SkillConflict): skills.set_enabled("compare", enabled=True, revision=item["revision"])


def test_save_requires_current_revision_and_always_disables(tmp_path):
    skills, item = approved(tmp_path)
    with pytest.raises(SkillConflict): skills.save("compare", draft())
    saved = skills.save("compare", draft(revision=item["revision"]))
    assert saved["revision"] == item["revision"] and not saved["enabled"]
    changed = skills.save("compare", draft(instructions="新版本。", revision=saved["revision"]))
    with pytest.raises(SkillConflict): skills.save("compare", draft(revision=saved["revision"]))
    assert skills.get("compare")["revision"] == changed["revision"]


@pytest.mark.parametrize("skill_id", ["../outside", "a/b", "UPPER", "", "a" * 49])
def test_id_never_selects_arbitrary_paths(tmp_path, skill_id):
    skills = PersonalSkills(str(tmp_path))
    with pytest.raises(ValueError): skills.save(skill_id, draft())
    assert not skills.base.exists()


@pytest.mark.parametrize("target", ["base", "directory", "file", "approval", "lock"])
def test_symlinks_cannot_load_or_write_external_files(tmp_path, target):
    root, outside = tmp_path / "general", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    marker = outside / "SKILL.md"; marker.write_text("PRIVATE-OUTSIDE-MARKER")
    skills, item = approved(root)
    if target == "base":
        import shutil
        shutil.rmtree(skills.base); skills.base.symlink_to(outside, target_is_directory=True)
    elif target == "directory":
        import shutil
        shutil.rmtree(skills.base / "compare"); (skills.base / "compare").symlink_to(outside, target_is_directory=True)
    else:
        leaf = {"file": "SKILL.md", "approval": "approval.json", "lock": ".lock"}[target]
        path = skills.base / "compare" / leaf
        path.unlink(); path.symlink_to(marker)
    if target == "approval":
        assert not skills.get("compare")["enabled"]
    else:
        with pytest.raises((ValueError, OSError)): skills.load_instructions("compare")
    with pytest.raises((ValueError, OSError)): skills.save("compare", draft(revision=item["revision"]))
    assert marker.read_text() == "PRIVATE-OUTSIDE-MARKER"


@pytest.mark.parametrize("approval", ["broken", "[]", '{"enabled": "true"}', '{"enabled": true, "revision": "old"}'])
def test_invalid_approval_is_fail_closed(tmp_path, approval):
    skills, _ = approved(tmp_path)
    (skills.base / "compare/approval.json").write_text(approval)
    assert not skills.get("compare")["enabled"]
    with pytest.raises(ValueError): skills.load_instructions("compare")


def test_oversize_and_parser_failure_are_bounded_and_redacted(tmp_path):
    skills, _ = approved(tmp_path)
    path = skills.base / "compare/SKILL.md"
    path.write_text("x" * (64 * 1024 + 1))
    snapshot = skills.list()
    assert snapshot["items"] == [] and snapshot["issues"]
    path.write_text("---\nname: [invalid metadata PRIVATE-MARKER\n---\nbody")
    assert "PRIVATE-MARKER" not in json.dumps(skills.list())
    with pytest.raises(ValueError): skills.save("large", draft(instructions="中" * 30000))


def test_yaml_punctuation_roundtrips_without_executing_hooks(tmp_path):
    skills = PersonalSkills(str(tmp_path))
    item = skills.save("compare", draft(name="步骤 --- 示例", description="说明: #保留\n换行", instructions="第一步\n---\n第二步"))
    preview = skills.get("compare", preview=True)
    assert preview["name"] == "步骤 --- 示例" and preview["description"] == "说明: #保留\n换行"
    assert preview["instructions"] == "第一步\n---\n第二步"
    assert not item["enabled"]


def test_concurrent_edits_have_one_revision_winner(tmp_path):
    skills, item = approved(tmp_path)
    def save(n):
        try:
            return skills.save("compare", draft(instructions=f"更新 {n}", revision=item["revision"]))
        except SkillConflict:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(save, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert not skills.get("compare")["enabled"]


@pytest.mark.asyncio
async def test_general_loads_approved_instructions_without_project_or_script_authority(tmp_path):
    skills, item = approved(tmp_path)
    project = tmp_path / "skills/project"; project.mkdir(parents=True)
    (project / "SKILL.md").write_text("PROJECT-PRIVATE-MARKER")
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general")
    assert {"list_personal_skills", "use_personal_skill"} <= set(agent.tools)
    assert {"save_skill", "use_skill", "run_command", "read_file", "enable_personal_skill"}.isdisjoint(agent.tools)
    listing = await agent.tools["list_personal_skills"].handler({})
    assert len(listing["items"]) == 1 and "PROJECT-PRIVATE-MARKER" not in json.dumps(listing)
    tool = agent.tools["use_personal_skill"]
    assert tool.untrusted_source and tool.external_content
    result = await tool.handler({"id": "compare"})
    assert result["stage"] == "instructions_loaded" and result["revision"] == item["revision"]
    assert "completed" not in result and result["ok"]
    skills.set_enabled("compare", enabled=False, revision=item["revision"])
    assert (await tool.handler({"id": "compare"}))["ok"] is False
    assert not (skills.base / "missing").exists()
    assert (await tool.handler({"id": "missing"}))["ok"] is False
    assert not (skills.base / "missing").exists()


@pytest.mark.parametrize("kind,scope,expected", [("web", "general", True), ("im", "general", True), ("cli", "general", False), ("web", "project", False), ("web", "scratch", False)])
def test_personal_tools_only_in_interactive_assistant_scope(tmp_path, kind, scope, expected):
    agent = build_session(str(tmp_path), kind=kind, workspace_scope=scope)
    assert ("use_personal_skill" in agent.tools) is expected


@pytest.mark.asyncio
async def test_api_requires_general_scope_and_exact_preview_revision(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from src.web.routers import assistant
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    with pytest.raises(HTTPException) as denied:
        await assistant.personal_skills_list()
    assert denied.value.status_code == 403
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    saved = await assistant.personal_skill_save("compare", draft())
    preview = await assistant.personal_skill_preview("compare")
    assert preview["revision"] == saved["revision"]
    with pytest.raises(HTTPException) as conflict:
        await assistant.personal_skill_enabled("compare", {"enabled": True, "revision": "old"})
    assert conflict.value.status_code == 409
    enabled = await assistant.personal_skill_enabled("compare", {"enabled": True, "revision": preview["revision"]})
    assert enabled["enabled"]
    assert (await assistant.personal_skills_list())["items"][0]["enabled"]
    with pytest.raises(HTTPException) as invalid:
        await assistant.personal_skill_enabled("compare", {"enabled": "true", "revision": preview["revision"]})
    assert invalid.value.status_code == 400 and str(tmp_path) not in invalid.value.detail


@pytest.mark.asyncio
async def test_manual_disable_model_invocation_is_enforced(tmp_path):
    skills, _ = approved(tmp_path)
    path = skills.base / "compare/SKILL.md"
    path.write_text('---\nname: compare\ndisable-model-invocation: true\nhooks:\n  pre_execute:\n    - command: touch never-executed\n---\n指令\n')
    item = skills.get("compare")
    skills.set_enabled("compare", enabled=True, revision=item["revision"])
    with pytest.raises(ValueError): skills.load_instructions("compare")
    assert not (tmp_path / "never-executed").exists()


def test_approved_hook_declarations_are_inert(tmp_path):
    skills, _ = approved(tmp_path)
    path = skills.base / "compare/SKILL.md"
    path.write_text('---\nname: compare\nhooks:\n  pre_execute:\n    - command: touch never-executed\n---\n指令\n')
    item = skills.get("compare")
    skills.set_enabled("compare", enabled=True, revision=item["revision"])
    assert skills.load_instructions("compare")["instructions"] == "指令"
    assert not (tmp_path / "never-executed").exists()


def test_failed_content_write_keeps_previous_version_disabled(tmp_path, monkeypatch):
    skills, item = approved(tmp_path)
    original = skills._write
    def failing(skill_id, leaf, raw):
        if leaf == "SKILL.md": raise OSError("synthetic private storage path")
        original(skill_id, leaf, raw)
    monkeypatch.setattr(skills, "_write", failing)
    with pytest.raises(OSError): skills.save("compare", draft(revision=item["revision"], instructions="新版本"))
    assert skills.get("compare")["revision"] == item["revision"] and not skills.get("compare")["enabled"]


def test_inventory_is_bounded_and_reports_truncation(tmp_path, monkeypatch):
    import src.workflow.workbench.personal_skills as module
    monkeypatch.setattr(module, "MAX_SKILLS", 2)
    skills = PersonalSkills(str(tmp_path))
    skills.save("first", draft()); skills.save("second", draft())
    directory = skills.base / "third"; directory.mkdir()
    (directory / "SKILL.md").write_text("未批准的手工文件")
    snapshot = skills.list()
    assert len(snapshot["items"]) == 2 and snapshot["truncated"]
    assert not any(item["enabled"] for item in snapshot["items"])


@pytest.mark.asyncio
async def test_loading_uses_original_events_and_taints_the_turn(tmp_path):
    from src.agents import taint
    from src.gateway.audit import list_audit, record_tool_audit
    _, item = approved(tmp_path)
    events = []
    agent = build_session(str(tmp_path), kind="web", workspace_scope="general",
                          on_tool_event=lambda stage, payload: events.append((stage, payload)),
                          on_tool=lambda name, args, result: record_tool_audit(str(tmp_path), session="synthetic", mode="plan", name=name, args=args, result=result))
    taint.reset_taint()
    try:
        output = await agent._run_tools([("use_personal_skill", {"id": "compare"})], "plan", lambda _: None)
        assert "instructions_loaded" in str(output) and item["revision"] in str(output)
        assert taint.is_tainted()
        assert any(stage == "finish" and payload["name"] == "use_personal_skill" for stage, payload in events)
        entries = list_audit(str(tmp_path))
        assert entries[0]["tool"] == "use_personal_skill"
        assert draft()["instructions"] not in json.dumps(entries, ensure_ascii=False)
    finally:
        taint.reset_taint()
