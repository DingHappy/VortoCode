"""项目级指令文件加载（src/agents/project，对标 AGENTS.md/CLAUDE.md）—— 纯离线。"""


from src.agents.project import (find_instructions_file,
                                load_project_instructions)


def test_no_file_returns_empty(tmp_path):
    assert load_project_instructions(str(tmp_path)) == ""
    assert find_instructions_file(str(tmp_path)) is None


def test_loads_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# 约定\n用中文注释；绝不碰 main。", encoding="utf-8")
    out = load_project_instructions(str(tmp_path))
    assert "项目指令" in out and "AGENTS.md" in out and "绝不碰 main" in out


def test_priority_agents_over_claude(tmp_path):
    (tmp_path / "AGENTS.md").write_text("opencode 风格", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("CC 风格", encoding="utf-8")
    out = load_project_instructions(str(tmp_path))
    assert "opencode 风格" in out and "CC 风格" not in out      # 优先 AGENTS.md


def test_falls_back_to_claude_then_vorto(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("CC 内容", encoding="utf-8")
    assert "CC 内容" in load_project_instructions(str(tmp_path))
    (tmp_path / "CLAUDE.md").unlink()
    (tmp_path / "VORTO.md").write_text("Vorto 内容", encoding="utf-8")
    assert "Vorto 内容" in load_project_instructions(str(tmp_path))


def test_empty_file_skipped(tmp_path):
    (tmp_path / "AGENTS.md").write_text("   \n  ", encoding="utf-8")
    assert load_project_instructions(str(tmp_path)) == ""


def test_oversized_truncated(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x" * 20000, encoding="utf-8")
    out = load_project_instructions(str(tmp_path))
    assert "已截断" in out and len(out) < 9000


def test_local_vortocode_agents(tmp_path):
    d = tmp_path / ".vortocode"
    d.mkdir()
    (d / "AGENTS.md").write_text("本地私有约定", encoding="utf-8")
    assert "本地私有约定" in load_project_instructions(str(tmp_path))
