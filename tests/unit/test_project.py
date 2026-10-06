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


# ---- 用户级全局指令（~/.vortocode/AGENTS.md）
from src.agents import project as project_module
from src.agents.project import load_user_instructions


def test_user_instructions_missing_or_blank_is_empty(tmp_path):
    assert load_user_instructions(tmp_path / "AGENTS.md") == ""
    (tmp_path / "AGENTS.md").write_text("  \n", encoding="utf-8")
    assert load_user_instructions(tmp_path / "AGENTS.md") == ""


def test_user_instructions_are_labelled_and_truncated(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("回答用中文", encoding="utf-8")
    text = load_user_instructions(path)
    assert text.startswith("【全局指令】") and "回答用中文" in text
    path.write_text("x" * 9000, encoding="utf-8")
    assert load_user_instructions(path).endswith("…(全局指令过长已截断)")


def test_user_instructions_reach_every_workspace_scope(tmp_path, monkeypatch):
    from src.gateway.agent_session import build_session
    instructions = tmp_path / "home" / "AGENTS.md"
    instructions.parent.mkdir()
    instructions.write_text("所有回答结尾加一句总结", encoding="utf-8")
    monkeypatch.setattr(project_module, "USER_INSTRUCTIONS_PATH", instructions)
    repo = tmp_path / "repo"
    repo.mkdir()
    for scope in ("general", "scratch", "project"):
        agent = build_session(str(repo), kind="web", workspace_scope=scope)
        assert "所有回答结尾加一句总结" in (agent.extra_system or ""), scope
