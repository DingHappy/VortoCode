"""Git workflow helpers used by TUI commands."""

import subprocess

from src.agents.git_workflow import (change_review,
                                     changed_test_selection,
                                     commit_changes,
                                     diff_hunks_for_review,
                                     diff_for_review,
                                     format_diff_hunks,
                                     format_change_review,
                                     format_preflight_report,
                                     format_pr_preview,
                                     format_status_summary,
                                     preflight_report,
                                     pr_preview,
                                     suggest_commit_message,
                                     status_summary)
from src.agents.verify_profiles import (format_verify_profiles,
                                        load_verify_profiles,
                                        recommend_verify_profiles,
                                        resolve_verify_profile)


def _git(path, *args):
    # 强制初始分支为 main：CI runner 的 git 默认分支可能是 master，
    # 会让依赖 base=main 的 pr_preview 找不到 base（init.defaultBranch 自 git 2.28 起支持）。
    if args and args[0] == "init":
        args = ("-c", "init.defaultBranch=main", *args)
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True)


def _init_repo(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "x@example.com")
    _git(path, "config", "user.name", "x")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")


def test_status_summary_reports_staged_and_unstaged(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(tmp_path, "add", "staged.txt")
    (tmp_path / "base.txt").write_text("changed\n", encoding="utf-8")

    info = status_summary(str(tmp_path))
    text = format_status_summary(info)

    assert info["ok"] is True
    assert "staged.txt" in info["porcelain"]
    assert "base.txt" in info["porcelain"]
    assert "已 staged diffstat" in text
    assert "未 staged diffstat" in text


def test_change_review_flags_source_without_tests(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("draft\n", encoding="utf-8")

    review = change_review(str(tmp_path))
    text = format_change_review(review)

    assert review["ok"] is True
    assert "app.py" in review["paths"]
    assert "notes.md" in review["untracked"]
    assert any("没有看到测试文件" in r for r in review["risks"])
    assert "建议下一步" in text


def test_change_review_cached_only_ignores_unstaged(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "staged.py").write_text("staged = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "staged.py")
    (tmp_path / "base.txt").write_text("unstaged\n", encoding="utf-8")

    review = change_review(str(tmp_path), cached=True)
    text = format_change_review(review)

    assert review["scope"] == "staged"
    assert "staged.py" in review["paths"]
    assert "base.txt" not in review["paths"]
    assert "已 staged" in text


def test_changed_test_selection_maps_source_to_unit_test(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src" / "agents").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "src" / "agents" / "sample.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "unit" / "test_sample.py").write_text("def test_x(): pass\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "add sample")
    (tmp_path / "src" / "agents" / "sample.py").write_text("x = 2\n", encoding="utf-8")

    sel = changed_test_selection(str(tmp_path))

    assert sel["ok"] is True
    assert sel["selectors"] == ["tests/unit/test_sample.py"]
    assert sel["fallback_full"] is False


def test_changed_test_selection_includes_changed_test_file(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_sample.py").write_text("def test_x(): pass\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "add test")
    (tmp_path / "tests" / "unit" / "test_sample.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    sel = changed_test_selection(str(tmp_path))

    assert sel["selectors"] == ["tests/unit/test_sample.py"]


def test_suggest_commit_message_for_staged_source_change(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "src" / "agents").mkdir(parents=True)
    (tmp_path / "src" / "agents" / "sample.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "add sample")
    (tmp_path / "src" / "agents" / "sample.py").write_text("x = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "src/agents/sample.py")

    msg = suggest_commit_message(str(tmp_path))

    assert msg["ok"] is True
    assert msg["message"] == "fix(agents): update agents"


def test_suggest_commit_message_stage_all_expands_untracked_dirs(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "src" / "agents").mkdir(parents=True)
    (tmp_path / "src" / "agents" / "new_tool.py").write_text("x = 1\n", encoding="utf-8")

    msg = suggest_commit_message(str(tmp_path), stage_all=True)

    assert msg["ok"] is True
    assert msg["paths"] == ["src/agents/new_tool.py"]
    assert msg["message"] == "feat(agents): update agents"


def test_preflight_report_combines_risks_tests_and_commit_message(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src" / "agents").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "src" / "agents" / "sample.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "unit" / "test_sample.py").write_text("def test_x(): pass\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "add sample")
    (tmp_path / "src" / "agents" / "sample.py").write_text("x = 2\n", encoding="utf-8")

    report = preflight_report(str(tmp_path))
    text = format_preflight_report(report)

    assert report["ok"] is True
    assert report["tests"]["selectors"] == ["tests/unit/test_sample.py"]
    assert report["commit"]["message"] == "fix(agents): update agents"
    assert "Preflight: 工作区" in text
    assert "/review" in text
    assert "/review --fix" in text
    assert "/verify unit" in text
    assert "/verify self-analyze" in text
    assert "/verify --changed" in text
    assert "/commit all --suggest" in text


def test_preflight_report_recommends_targeted_builtin_profile(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "src" / "tui").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "src" / "tui" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "unit" / "test_tui.py").write_text("def test_x(): pass\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "add tui")
    (tmp_path / "src" / "tui" / "app.py").write_text("x = 2\n", encoding="utf-8")

    report = preflight_report(str(tmp_path))
    text = format_preflight_report(report)

    assert report["verify_profiles"]["ok"] is True
    assert [r["name"] for r in report["verify_profiles"]["recommendations"]][:2] == ["tui", "unit"]
    assert "/verify tui" in text
    assert "TUI 相关文件有改动" in text


def test_diff_for_review_returns_bounded_diff(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "base.txt").write_text("changed\n", encoding="utf-8")

    payload = diff_for_review(str(tmp_path), limit=80)

    assert payload["ok"] is True
    assert payload["scope"] == "workspace"
    assert "diff --git" in payload["diff"]
    assert "base.txt" in payload["diff"]


def test_diff_hunks_for_review_lists_and_selects_hunks(tmp_path):
    _init_repo(tmp_path)
    lines = [f"line {i}\n" for i in range(30)]
    (tmp_path / "base.txt").write_text("".join(lines), encoding="utf-8")
    _git(tmp_path, "add", "base.txt")
    _git(tmp_path, "commit", "-qm", "expand base")
    lines[1] = "line two changed\n"
    lines[25] = "line twenty five changed\n"
    (tmp_path / "base.txt").write_text("".join(lines), encoding="utf-8")

    payload = diff_hunks_for_review(str(tmp_path))
    text = format_diff_hunks(payload)
    selected = diff_for_review(str(tmp_path), hunk_id="H2")

    assert payload["ok"] is True
    assert [h["id"] for h in payload["hunks"]] == ["H1", "H2"]
    assert "H1 base.txt" in text
    assert "H2 base.txt" in text
    assert selected["ok"] is True
    assert selected["hunk"]["id"] == "H2"
    assert "line twenty five changed" in selected["diff"]
    assert "line two changed" not in selected["diff"]


def test_diff_for_review_rejects_empty_changes(tmp_path):
    _init_repo(tmp_path)

    payload = diff_for_review(str(tmp_path))

    assert payload["ok"] is False
    assert "没有可审查" in payload["error"]


def test_verify_profiles_load_project_yaml_and_resolve(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "verify.yaml").write_text(
        "profiles:\n"
        "  smoke:\n"
        "    cmd: python main.py self-analyze\n"
        "    description: quick scan\n",
        encoding="utf-8",
    )

    loaded = load_verify_profiles(str(tmp_path))
    text = format_verify_profiles(loaded)
    resolved = resolve_verify_profile(str(tmp_path), "SMOKE")

    assert loaded["ok"] is True
    assert loaded["profiles"]["smoke"]["cmd"] == "python main.py self-analyze"
    assert "smoke [project]" in text
    assert resolved["ok"] is True
    assert resolved["name"] == "smoke"


def test_verify_profiles_recommend_project_path_match(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "verify.yaml").write_text(
        "profiles:\n"
        "  docs:\n"
        "    cmd: python -m docs_check\n"
        "    paths: [docs/**]\n",
        encoding="utf-8",
    )

    rec = recommend_verify_profiles(str(tmp_path), ["docs/WORK_PLAN.md"])

    assert rec["ok"] is True
    assert rec["recommendations"][0]["name"] == "docs"
    assert rec["recommendations"][0]["reason"] == "匹配 docs/**"


def test_commit_changes_staged_only(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")

    result = commit_changes(str(tmp_path), "add a")

    assert result["ok"] is True
    assert result["sha"]
    assert "add a" in _git(tmp_path, "log", "-1", "--pretty=%s").stdout


def test_commit_changes_all_stages_before_commit(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")

    result = commit_changes(str(tmp_path), "add all", stage_all=True)

    assert result["ok"] is True
    assert "add all" in _git(tmp_path, "log", "-1", "--pretty=%s").stdout


def test_commit_changes_rejects_no_staged_changes(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")

    result = commit_changes(str(tmp_path), "add a")

    assert result["ok"] is False
    assert "没有 staged" in result["error"]


def test_pr_preview_builds_title_body_and_file_list(tmp_path):
    _init_repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature/demo")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "feat: add a")

    preview = pr_preview(str(tmp_path), base="main")
    text = format_pr_preview(preview)

    assert preview["ok"] is True
    assert preview["branch"] == "feature/demo"
    assert preview["base"] == "main"
    assert preview["title"] == "feat: add a"
    assert "a.txt" in preview["changed_files"]
    assert "## Summary" in preview["body"]
    assert "PR 预览: feature/demo → main" in text


def test_pr_preview_rejects_main_branch(tmp_path):
    _init_repo(tmp_path)

    preview = pr_preview(str(tmp_path), base="main")

    assert preview["ok"] is False
    assert "当前分支是" in preview["error"]


def test_pr_preview_rejects_dirty_worktree(tmp_path):
    _init_repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature/demo")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")

    preview = pr_preview(str(tmp_path), base="main")

    assert preview["ok"] is False
    assert "未提交改动" in preview["error"]
