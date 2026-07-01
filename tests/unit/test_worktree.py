"""隔离实现地基：git worktree 生命周期 + 根限定写工具（src/agents/worktree.py, build_write_tools）。

用真实的临时 git 仓库验证：改动只落在一次性 worktree、产出 diff、无论成败都清理。
"""
import subprocess

import pytest

from src.agents import worktree
from src.agents.main_agent import build_write_tools


def _init_repo(path):
    def git(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (path / "a.txt").write_text("hello\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


@pytest.mark.asyncio
async def test_in_worktree_isolates_and_collects_diff(tmp_path):
    _init_repo(tmp_path)

    async def work(wt):
        (wt / "new.py").write_text("print('hi')\n")        # 新文件
        (wt / "a.txt").write_text("changed\n")              # 改已有
        return "done"

    diff, result = await worktree.in_worktree(str(tmp_path), "wt-test", work)
    assert result == "done"
    assert "new.py" in diff and "print('hi')" in diff       # 新文件进 diff
    assert "changed" in diff                                 # 改动进 diff
    # 隔离：主工作区没被碰
    assert (tmp_path / "a.txt").read_text() == "hello\n"
    assert not (tmp_path / "new.py").exists()
    # worktree 已清理
    assert not (tmp_path / ".vortocode" / "worktrees" / "wt-test").exists()


@pytest.mark.asyncio
async def test_collect_diff_excludes_build_artifacts_and_lands(tmp_path):
    # 回归（真实历练发现的 bug）：目标仓库**没有 .gitignore** 时，隔离自测生成的
    # __pycache__/*.pyc 会随 git add -A 进 diff，落分支的 git apply 栽在二进制补丁上。
    # collect_diff 须兜底排除这些噪音，diff 干净、能正常落分支。
    _init_repo(tmp_path)                                     # _init_repo 不建 .gitignore

    async def work(wt):
        (wt / "mod.py").write_text("def f():\n    return 1\n")   # 真实源码改动
        cache = wt / "__pycache__"
        cache.mkdir()
        (cache / "mod.cpython-311.pyc").write_bytes(b"\x00\x01\x02BIN\xff")  # 自测产物（二进制）
        (wt / "sub").mkdir()
        (wt / "sub" / "__pycache__").mkdir()
        (wt / "sub" / "__pycache__" / "x.pyc").write_bytes(b"\xfe\xednested")  # 嵌套也得挡
        (wt / "top.pyc").write_bytes(b"\x00\xfftop")             # 顶层 .pyc
        return None

    diff, _ = await worktree.in_worktree(str(tmp_path), "wt-artifact", work)
    assert "mod.py" in diff and "return 1" in diff              # 源码改动进 diff
    assert "__pycache__" not in diff and ".pyc" not in diff     # 构建噪音被挡在 diff 外
    # 关键：含二进制噪音本会让 git apply 失败；排除后落分支必须成功（绿了能交付）
    res = worktree.apply_diff_to_branch(str(tmp_path), "vorto/artifact", diff, "add mod")
    assert res["ok"] is True, res.get("error")
    tree = subprocess.run(["git", "-C", str(tmp_path), "ls-tree", "-r", "--name-only", "vorto/artifact"],
                          capture_output=True, text=True).stdout
    assert "mod.py" in tree and ".pyc" not in tree             # 分支里有源码、无 .pyc


@pytest.mark.asyncio
async def test_in_worktree_cleans_up_on_error(tmp_path):
    _init_repo(tmp_path)

    async def boom(wt):
        (wt / "x.py").write_text("x")
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        await worktree.in_worktree(str(tmp_path), "wt-err", boom)
    assert not (tmp_path / ".vortocode" / "worktrees" / "wt-err").exists()   # 出错也清理


@pytest.mark.asyncio
async def test_run_isolated_task_with_injected_agent(tmp_path):
    _init_repo(tmp_path)

    class FakeAgent:
        def __init__(self, root):
            self.root = root

        async def run_turn(self, desc, mode, emit):
            from pathlib import Path
            (Path(self.root) / "impl.py").write_text("# " + desc + "\n")
            return "实现完成"

    diff, conclusion, ver = await worktree.run_isolated_task(
        str(tmp_path), "wt-run", "加个模块", lambda root: FakeAgent(root))
    assert conclusion == "实现完成"
    assert "impl.py" in diff and "加个模块" in diff
    assert ver is None                                      # 没给 test_cmd → 不验证
    assert not (tmp_path / "impl.py").exists()              # 主工作区干净


@pytest.mark.asyncio
async def test_build_write_tools_edit_write_and_escape_guard(tmp_path):
    (tmp_path / "f.py").write_text("a=1\n")
    tools = {t.name: t for t in build_write_tools(str(tmp_path))}

    await tools["edit_file"].handler({"path": "f.py", "old": "a=1", "new": "a=2"})
    assert (tmp_path / "f.py").read_text() == "a=2\n"

    await tools["write_file"].handler({"path": "sub/new.py", "content": "x\n"})
    assert (tmp_path / "sub" / "new.py").read_text() == "x\n"

    r = await tools["write_file"].handler({"path": "../escape.py", "content": "x"})
    assert "越界" in r and not (tmp_path.parent / "escape.py").exists()   # 不写出 root 之外

    assert all(t.read_only is False for t in build_write_tools(str(tmp_path)))   # 是写工具


@pytest.mark.asyncio
async def test_edit_file_replace_all_and_uniqueness(tmp_path):
    (tmp_path / "f.py").write_text("x = OLD\ny = OLD\nz = OLD\n", encoding="utf-8")
    edit = {t.name: t for t in build_write_tools(str(tmp_path))}["edit_file"]

    # 默认（不唯一）→ 拒绝、提示可 replace_all，且不改文件
    r = await edit.handler({"path": "f.py", "old": "OLD", "new": "NEW"})
    assert "出现 3 次" in r and "replace_all" in r
    assert (tmp_path / "f.py").read_text(encoding="utf-8").count("OLD") == 3   # 没动

    # replace_all=true → 一次替换全部
    r2 = await edit.handler({"path": "f.py", "old": "OLD", "new": "NEW", "replace_all": True})
    assert "替换 3 处" in r2
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "x = NEW\ny = NEW\nz = NEW\n"


@pytest.mark.asyncio
async def test_edit_file_replace_all_accepts_string_flag(tmp_path):
    # 提示式协议里 flag 是字符串 "true"（非原生 bool）也要认
    (tmp_path / "g.py").write_text("A\nA\n", encoding="utf-8")
    edit = {t.name: t for t in build_write_tools(str(tmp_path))}["edit_file"]
    r = await edit.handler({"path": "g.py", "old": "A", "new": "B", "replace_all": "true"})
    assert "替换 2 处" in r and (tmp_path / "g.py").read_text(encoding="utf-8") == "B\nB\n"


@pytest.mark.asyncio
async def test_edit_file_unique_still_replaces_one(tmp_path):
    # 唯一时一切照旧：替 1 处（即便没传 replace_all）
    (tmp_path / "h.py").write_text("only=ONE\nkeep=1\n", encoding="utf-8")
    edit = {t.name: t for t in build_write_tools(str(tmp_path))}["edit_file"]
    r = await edit.handler({"path": "h.py", "old": "ONE", "new": "TWO"})
    assert "替换 1 处" in r and (tmp_path / "h.py").read_text(encoding="utf-8") == "only=TWO\nkeep=1\n"


@pytest.mark.asyncio
async def test_run_isolated_task_with_real_main_agent(monkeypatch, tmp_path):
    # 真集成：真 MainAgent（只读+写工具）+ 假 LLM，在隔离 worktree 里写文件 → 产出 diff、清理
    import src.llm.client as llmmod
    from src.agents.main_agent import MainAgent, build_read_tools, build_write_tools
    _init_repo(tmp_path)

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:                       # 第一步：写个文件
                return {"content": '{"tool":"write_file","args":{"path":"hello.py","content":"print(1)\\n"}}'}
            return {"content": "加了 hello.py。"}  # 第二步：收口

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)

    def _build(wt):
        return MainAgent(build_read_tools(wt) + build_write_tools(wt), max_steps=6)

    diff, conclusion, ver = await worktree.run_isolated_task(str(tmp_path), "wt-real", "加 hello.py", _build)
    assert "hello.py" in diff and "print(1)" in diff      # 子 agent 的写改动进了 diff
    assert "加了 hello.py" in conclusion
    assert not (tmp_path / "hello.py").exists()            # 主工作区干净


@pytest.mark.asyncio
async def test_apply_diff_to_branch_creates_branch_without_touching_main(tmp_path):
    _init_repo(tmp_path)

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)

    cur = git("branch", "--show-current").stdout.strip()

    async def work(wt):
        (wt / "feat.py").write_text("x = 1\n")
        return None
    diff, _ = await worktree.in_worktree(str(tmp_path), "wt-diff", work)

    res = worktree.apply_diff_to_branch(str(tmp_path), "vorto/feat-x", diff, "add feat")
    assert res["ok"] is True and res["branch"] == "vorto/feat-x"
    assert "feat.py" in git("ls-tree", "-r", "--name-only", "vorto/feat-x").stdout   # 分支里有新文件
    assert not (tmp_path / "feat.py").exists()                                        # 主工作区没被碰
    assert git("branch", "--show-current").stdout.strip() == cur                      # 当前分支没切走
    wt_dir = tmp_path / ".vortocode" / "worktrees"
    assert not wt_dir.exists() or not any(wt_dir.iterdir())                           # 临时 worktree 已清


def test_apply_diff_to_branch_bad_diff_cleans_up(tmp_path):
    _init_repo(tmp_path)
    res = worktree.apply_diff_to_branch(str(tmp_path), "vorto/bad", "这不是合法 diff\n", "x")
    assert res["ok"] is False and "apply" in res["error"]
    branches = subprocess.run(["git", "-C", str(tmp_path), "branch", "--list", "vorto/bad"],
                              capture_output=True, text=True).stdout
    assert "vorto/bad" not in branches                                                # 残留空分支被删


@pytest.mark.asyncio
async def test_apply_diffs_to_branch_multiple_independent(tmp_path):
    _init_repo(tmp_path)

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)

    async def w1(wt):
        (wt / "f1.py").write_text("a = 1\n")

    async def w2(wt):
        (wt / "f2.py").write_text("b = 2\n")
    d1, _ = await worktree.in_worktree(str(tmp_path), "wt-1", w1)
    d2, _ = await worktree.in_worktree(str(tmp_path), "wt-2", w2)

    res = worktree.apply_diffs_to_branch(str(tmp_path), "vorto/multi", [(d1, "add f1"), (d2, "add f2")])
    assert res["ok"] and len(res["applied"]) == 2 and not res["failed"]
    tree = git("ls-tree", "-r", "--name-only", "vorto/multi").stdout
    assert "f1.py" in tree and "f2.py" in tree                          # 两块都进了分支
    log = git("log", "--oneline", "vorto/multi").stdout
    assert "add f1" in log and "add f2" in log                          # 各自一个提交
    assert not (tmp_path / "f1.py").exists()                            # 主工作区没被碰


@pytest.mark.asyncio
async def test_apply_diffs_partial_failure_keeps_good_ones(tmp_path):
    _init_repo(tmp_path)

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)

    async def wg(wt):
        (wt / "g.py").write_text("ok\n")
    good, _ = await worktree.in_worktree(str(tmp_path), "wt-g", wg)

    res = worktree.apply_diffs_to_branch(
        str(tmp_path), "vorto/partial", [(good, "good"), ("这不是 diff\n", "bad")])
    assert "good" in res["applied"] and len(res["failed"]) == 1         # 好的进了、坏的记账跳过
    assert res["ok"] is True                                            # 有成功的就算 ok
    assert "g.py" in git("ls-tree", "-r", "--name-only", "vorto/partial").stdout


@pytest.mark.asyncio
async def test_apply_diffs_3way_recovers_context_conflict(tmp_path):
    # 两块改同一文件的不同位置，但后一块的 diff 上下文被前一块改过 → 直 apply 会被拒、3way 救回
    _init_repo(tmp_path)

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)
    (tmp_path / "m.py").write_text("a\nb\nc\nd\ne\nf\ng\n")
    git("add", "-A"); git("commit", "-q", "-m", "add m")

    async def w1(wt):
        (wt / "m.py").write_text("a\nb\nC_CHANGED\nd\ne\nf\ng\n")    # 改中间的 c

    async def w2(wt):
        (wt / "m.py").write_text("A_CHANGED\nb\nc\nd\ne\nf\ng\n")    # 改顶部 a（hunk 上下文含 b,c,d）
    d1, _ = await worktree.in_worktree(str(tmp_path), "wt-c1", w1)
    d2, _ = await worktree.in_worktree(str(tmp_path), "wt-c2", w2)

    res = worktree.apply_diffs_to_branch(str(tmp_path), "vorto/3way", [(d1, "chg c"), (d2, "chg a")])
    assert len(res["applied"]) == 2 and not res["failed"]            # 3way 把第二块也合上了
    content = git("show", "vorto/3way:m.py").stdout
    assert "A_CHANGED" in content and "C_CHANGED" in content         # 两处改动都在
    assert "<<<<<<<" not in content                                  # 没有冲突 marker


@pytest.mark.asyncio
async def test_apply_diffs_integration_pass(tmp_path):
    # 各块干净落分支后，在集成分支上再跑一遍测试 → 全绿 → integration.ok True
    import sys
    _init_repo(tmp_path)
    (tmp_path / "n.py").write_text("V = 1\n")
    (tmp_path / "test_n.py").write_text("from n import V\ndef test_v(): assert V >= 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "base"], check=True, capture_output=True)

    async def w(wt):
        (wt / "n.py").write_text("V = 2\n")                 # 改后仍满足 V>=1
    d, _ = await worktree.in_worktree(str(tmp_path), "wt-n", w)

    test_cmd = [sys.executable, "-m", "pytest", "-q", "test_n.py"]
    res = worktree.apply_diffs_to_branch(str(tmp_path), "vorto/integ-ok", [(d, "bump V")], test_cmd)
    assert res["applied"] == ["bump V"]
    assert res["integration"] is not None and res["integration"]["ok"] is True   # 集成测试通过


@pytest.mark.asyncio
async def test_apply_diffs_integration_fail_keeps_branch(tmp_path):
    # 两块改不同文件、各自干净 apply，但合到一起破坏了共用测试（单独绿、合起来红）→ integration.ok False、分支保留
    import sys
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "b.py").write_text("B = 1\n")
    # 共用测试：A+B 不得超过 3；各自 +1 后单独都 ≤3，合起来 =4 越界
    (tmp_path / "test_ab.py").write_text(
        "from a import A\nfrom b import B\ndef test_sum(): assert A + B <= 3\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "base"], check=True, capture_output=True)

    async def wa(wt):
        (wt / "a.py").write_text("A = 2\n")                 # 只改 a.py：单独 A+B=3 ≤3
    async def wb(wt):
        (wt / "b.py").write_text("B = 2\n")                 # 只改 b.py：单独 A+B=3 ≤3
    da, _ = await worktree.in_worktree(str(tmp_path), "wt-a", wa)
    db, _ = await worktree.in_worktree(str(tmp_path), "wt-b", wb)

    test_cmd = [sys.executable, "-m", "pytest", "-q", "test_ab.py"]
    res = worktree.apply_diffs_to_branch(str(tmp_path), "vorto/integ-bad", [(da, "A"), (db, "B")], test_cmd)
    assert len(res["applied"]) == 2                          # 改不同文件 → 两块都干净 apply
    # 但合起来 A+B=4 >3 → 集成测试红（单独绿、合起来红，这正是要抓的）
    assert res["integration"] is not None and res["integration"]["ok"] is False
    assert "test_ab" in res["integration"]["output"] or "fail" in res["integration"]["output"].lower()
    # 红了不删分支：保留待修
    branches = subprocess.run(["git", "-C", str(tmp_path), "branch", "--list", "vorto/integ-bad"],
                              capture_output=True, text=True).stdout
    assert "vorto/integ-bad" in branches


@pytest.mark.asyncio
async def test_apply_diffs_no_test_cmd_integration_none(tmp_path):
    # 不给 test_cmd → integration 为 None（向后兼容，老调用方不变）
    _init_repo(tmp_path)

    async def w(wt):
        (wt / "z.py").write_text("z = 1\n")
    d, _ = await worktree.in_worktree(str(tmp_path), "wt-z", w)
    res = worktree.apply_diffs_to_branch(str(tmp_path), "vorto/nointeg", [(d, "add z")])
    assert res["applied"] == ["add z"] and res["integration"] is None


@pytest.mark.asyncio
async def test_apply_diffs_true_conflict_skipped_no_markers(tmp_path):
    # 两块改同一行 = 真冲突：第二块跳过、记账；分支里只留第一块，绝不提交冲突 marker
    _init_repo(tmp_path)

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)
    (tmp_path / "m.py").write_text("a\nb\nc\nd\ne\n")
    git("add", "-A"); git("commit", "-q", "-m", "add m")

    async def w1(wt):
        (wt / "m.py").write_text("a\nb\nC1\nd\ne\n")

    async def w2(wt):
        (wt / "m.py").write_text("a\nb\nC2\nd\ne\n")
    d1, _ = await worktree.in_worktree(str(tmp_path), "wt-t1", w1)
    d2, _ = await worktree.in_worktree(str(tmp_path), "wt-t2", w2)

    res = worktree.apply_diffs_to_branch(str(tmp_path), "vorto/conflict", [(d1, "c1"), (d2, "c2")])
    assert "c1" in res["applied"] and len(res["failed"]) == 1        # 真冲突 → 第二块跳过
    content = git("show", "vorto/conflict:m.py").stdout
    assert "C1" in content and "C2" not in content and "<<<<<<<" not in content  # 第一块在、无 marker
    # reset 干净：只多了一个提交（init + add m + c1 = 3），失败块没留半成品提交
    assert len(git("log", "--oneline", "vorto/conflict").stdout.strip().splitlines()) == 3


@pytest.mark.asyncio
async def test_build_dev_tools_lands_green_on_branch(monkeypatch, tmp_path):
    # UI 无关的 dev_isolated（Web 用）：实现+验证通过 → 自动落到 vorto/ 分支，不碰 main
    import sys
    import src.llm.client as llmmod
    from src.agents.main_agent import build_dev_tools

    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pass.py").write_text("def test_ok():\n    assert True\n")
    git("add", "-A"); git("commit", "-q", "-m", "init")

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:
                return {"content": '{"tool":"write_file","args":{"path":"feat.py","content":"x = 1\\n"}}'}
            return {"content": "加了 feat.py。"}
    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)

    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_isolated"]
    out = await tool.handler({"description": "加 feat 模块", "test": "tests/test_pass.py"})
    assert "✅" in out and "vorto/" in out                      # 绿了、落到分支
    branches = subprocess.run(["git", "-C", str(tmp_path), "branch", "--list", "vorto/*"],
                              capture_output=True, text=True).stdout
    assert "vorto/" in branches                                 # 分支建出来了
    assert not (tmp_path / "feat.py").exists()                  # 主工作区没被碰


@pytest.mark.asyncio
async def test_build_dev_tools_parallel_empty_guard(tmp_path):
    from src.agents.main_agent import build_dev_tools
    tools = {t.name: t for t in build_dev_tools(str(tmp_path))}
    assert "dev_parallel" in tools
    assert "需要 tasks" in await tools["dev_parallel"].handler({"tasks": []})   # 空输入有守卫


@pytest.mark.asyncio
async def test_dev_parallel_reports_integration_failure(monkeypatch, tmp_path):
    # _dev_parallel：各块单独绿、但集成后全量红 → 如实报"集成后全量测试未过"+失败尾部，不谎报全绿
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools

    async def fake_run_isolated(repo_root, wid, desc, build, test_cmd=None):
        return ("diff --git a/x b/x\n", f"做了 {desc}", {"ok": True, "output": "", "cmd": "pytest"})

    def fake_apply(repo_root, branch, items, test_cmd=None):
        return {"ok": True, "branch": branch, "applied": [m for _d, m in items], "failed": [],
                "integration": {"ok": False, "output": "BOOM_integration_failed", "cmd": "pytest"}}
    monkeypatch.setattr(wt, "run_isolated_task", fake_run_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch", fake_apply)

    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_parallel"]
    out = await tool.handler({"tasks": ["子任务甲", "子任务乙"]})
    assert "集成后全量测试未过" in out and "BOOM_integration_failed" in out
    assert "分支已保留待修" in out                                   # 红了保留分支供修


@pytest.mark.asyncio
async def test_dev_parallel_reports_integration_pass_and_passes_test_cmd(monkeypatch, tmp_path):
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools
    seen = {}

    async def fake_run_isolated(repo_root, wid, desc, build, test_cmd=None):
        return ("diff\n", "ok", {"ok": True, "output": "", "cmd": "pytest"})

    def fake_apply(repo_root, branch, items, test_cmd=None):
        seen["test_cmd"] = test_cmd                      # 确认 dev_parallel 把 test_cmd 传下去做集成验证
        return {"ok": True, "branch": branch, "applied": [m for _d, m in items], "failed": [],
                "integration": {"ok": True, "output": "", "cmd": "pytest"}}
    monkeypatch.setattr(wt, "run_isolated_task", fake_run_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch", fake_apply)

    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_parallel"]
    out = await tool.handler({"tasks": ["甲", "乙"]})
    assert "集成后全量测试通过" in out
    assert seen["test_cmd"] is not None                 # 集成验证用的 test_cmd 已传入


# ---- dev_auto 依赖接力：worktree 新原语（真实 git）----

@pytest.mark.asyncio
async def test_ensure_branch_creates_at_head_idempotent(tmp_path):
    _init_repo(tmp_path)

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)
    worktree.ensure_branch(str(tmp_path), "vorto/base")
    assert "vorto/base" in git("branch", "--list", "vorto/base").stdout
    assert git("rev-parse", "vorto/base").stdout.strip() == git("rev-parse", "HEAD").stdout.strip()
    worktree.ensure_branch(str(tmp_path), "vorto/base")             # 再来一次：幂等不报错
    assert "vorto/base" in git("branch", "--list", "vorto/base").stdout


@pytest.mark.asyncio
async def test_run_dependent_on_branch_green_commits_and_advances_tip(tmp_path):
    _init_repo(tmp_path)

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)
    worktree.ensure_branch(str(tmp_path), "vorto/dep")
    before = git("rev-parse", "vorto/dep").stdout.strip()

    class FakeAgent:
        def __init__(self, root):
            self.root = root

        async def run_turn(self, desc, mode, emit):
            from pathlib import Path
            (Path(self.root) / "dep.py").write_text("v = 1\n")
            return "做了"

    r = await worktree.run_dependent_on_branch(
        str(tmp_path), "wt-dep", "vorto/dep", "加 dep", lambda root: FakeAgent(root),
        "dep commit", test_cmd=None)
    assert r["ok"] is True
    assert git("rev-parse", "vorto/dep").stdout.strip() != before    # tip 推进
    assert "dep.py" in git("ls-tree", "-r", "--name-only", "vorto/dep").stdout
    assert "dep commit" in git("log", "--oneline", "vorto/dep").stdout
    assert not (tmp_path / "dep.py").exists()                        # 主工作区没碰
    assert not (tmp_path / ".vortocode" / "worktrees" / "wt-dep").exists()   # worktree 清理


@pytest.mark.asyncio
async def test_run_dependent_on_branch_red_does_not_commit(tmp_path):
    import sys
    _init_repo(tmp_path)

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)
    (tmp_path / "test_fail.py").write_text("def test_x():\n    assert False\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "failing test"],
                   check=True, capture_output=True)
    worktree.ensure_branch(str(tmp_path), "vorto/depred")
    before = git("rev-parse", "vorto/depred").stdout.strip()

    class FakeAgent:
        def __init__(self, root):
            self.root = root

        async def run_turn(self, desc, mode, emit):
            from pathlib import Path
            (Path(self.root) / "x.py").write_text("y = 1\n")
            return "做了"

    test_cmd = [sys.executable, "-m", "pytest", "-q", "test_fail.py"]
    r = await worktree.run_dependent_on_branch(
        str(tmp_path), "wt-r", "vorto/depred", "改点东西", lambda root: FakeAgent(root),
        "should not commit", test_cmd)
    assert r["ok"] is False                                          # 自测红
    assert git("rev-parse", "vorto/depred").stdout.strip() == before  # 没提交、tip 不动
    assert "x.py" not in git("ls-tree", "-r", "--name-only", "vorto/depred").stdout


@pytest.mark.asyncio
async def test_verify_branch_runs_tests_and_cleans_up(tmp_path):
    import sys
    _init_repo(tmp_path)

    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    git("add", "-A"); git("commit", "-q", "-m", "add test")
    worktree.ensure_branch(str(tmp_path), "vorto/ver")
    test_cmd = [sys.executable, "-m", "pytest", "-q", "test_ok.py"]
    res = worktree.verify_branch(str(tmp_path), "vorto/ver", test_cmd, "wt-ver")
    assert res["ok"] is True
    assert not (tmp_path / ".vortocode" / "worktrees" / "wt-ver").exists()


@pytest.mark.asyncio
async def test_dev_auto_independent_then_dependent_topo_then_verify(monkeypatch, tmp_path):
    # dev_auto 端到端编排：独立批并行 → 依赖批按拓扑序接力(B→C) → 最终集成验证 → 如实汇报
    import src.agents.worktree as wt
    import src.agents.decompose as dec
    from src.agents.main_agent import build_dev_tools
    from src.orchestrator.task_analyzer import SubTask

    a = SubTask(id="a", title="A")
    b = SubTask(id="b", title="B", dependencies=["a"])
    c = SubTask(id="c", title="C", dependencies=["b"])

    async def fake_decompose(task, **k):
        return {"descriptions": ["实现 A"], "independent": [a], "deferred": [c, b], "total": 3}
    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)

    async def fake_isolated(repo, wid, desc, build, test_cmd=None):
        return ("diff\n", "ok", {"ok": True, "output": "", "cmd": "pytest"})
    monkeypatch.setattr(wt, "run_isolated_task", fake_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: {"ok": True, "branch": br,
                                                          "applied": [m for _d, m in items],
                                                          "failed": [], "integration": None})
    calls = []

    async def fake_dep(repo, wid, branch, desc, build, msg, test_cmd=None):
        calls.append(desc)
        return {"ok": True, "conclusion": "done", "output": ""}
    monkeypatch.setattr(wt, "run_dependent_on_branch", fake_dep)
    monkeypatch.setattr(wt, "verify_branch",
                        lambda repo, br, tc, wid: {"ok": True, "output": "", "cmd": "pytest"})

    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_auto"]
    out = await tool.handler({"task": "做个大功能"})
    assert "独立批" in out and "依赖接力" in out and "集成后全量测试通过" in out
    assert len(calls) == 2 and "B" in calls[0] and "C" in calls[1]   # 拓扑序：B 先于 C（虽输入是 c,b）


@pytest.mark.asyncio
async def test_dev_auto_reports_final_integration_failure(monkeypatch, tmp_path):
    import src.agents.worktree as wt
    import src.agents.decompose as dec
    from src.agents.main_agent import build_dev_tools
    from src.orchestrator.task_analyzer import SubTask

    a = SubTask(id="a", title="A")

    async def fake_decompose(task, **k):
        return {"descriptions": ["实现 A"], "independent": [a], "deferred": [], "total": 1}
    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)

    async def fake_isolated(repo, wid, desc, build, test_cmd=None):
        return ("diff\n", "ok", {"ok": True, "output": "", "cmd": "pytest"})
    monkeypatch.setattr(wt, "run_isolated_task", fake_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: {"ok": True, "branch": br,
                                                          "applied": [m for _d, m in items],
                                                          "failed": [], "integration": None})
    monkeypatch.setattr(wt, "verify_branch",
                        lambda repo, br, tc, wid: {"ok": False, "output": "FINAL_INTEG_RED", "cmd": "pytest"})

    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_auto"]
    out = await tool.handler({"task": "做个功能"})
    assert "集成后全量测试未过" in out and "FINAL_INTEG_RED" in out and "分支保留待修" in out


# ---- 失败子任务自修复重试 ----

@pytest.mark.asyncio
async def test_dev_parallel_self_repairs_on_failure(monkeypatch, tmp_path):
    # 子任务第一次自测红 → 带失败反馈、换全新 worktree 自动重试 → 第二次绿 → 算通过
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools
    state = {"n": 0, "descs": []}

    async def flaky_isolated(repo, wid, desc, build, test_cmd=None):
        state["n"] += 1
        state["descs"].append(desc)
        if state["n"] == 1:
            return ("diff\n", "c", {"ok": False, "output": "FAILED_ONCE_XYZ"})   # 第一次红
        return ("diff\n", "c", {"ok": True, "output": ""})                        # 修复后绿
    monkeypatch.setattr(wt, "run_isolated_task", flaky_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: {"ok": True, "branch": br,
                                                          "applied": [m for _d, m in items], "failed": [],
                                                          "integration": {"ok": True, "output": "", "cmd": "p"}})
    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_parallel"]
    out = await tool.handler({"tasks": ["实现X"]})
    assert state["n"] == 2                                   # 红 → 自动重试一次
    assert "FAILED_ONCE_XYZ" in state["descs"][1]           # 第二次把失败输出拼进了描述（定向修复）
    assert "1 通过测试" in out and "修复 1 次后" in out       # 修复后算通过、标了修复次数


@pytest.mark.asyncio
async def test_dev_parallel_all_attempts_fail_reports_red(monkeypatch, tmp_path):
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools
    state = {"n": 0}

    async def always_red(repo, wid, desc, build, test_cmd=None):
        state["n"] += 1
        return ("diff\n", "c", {"ok": False, "output": "STILL_RED"})
    monkeypatch.setattr(wt, "run_isolated_task", always_red)
    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_parallel"]
    out = await tool.handler({"tasks": ["x"]})
    assert state["n"] == 2                                   # 默认尝试 2 次（1 初始 + 1 修复）后放弃
    assert "无通过测试的改动" in out and "试了 2 次" in out


@pytest.mark.asyncio
async def test_dev_attempts_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "3")
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools
    state = {"n": 0}

    async def always_red(repo, wid, desc, build, test_cmd=None):
        state["n"] += 1
        return ("diff\n", "c", {"ok": False, "output": "R"})
    monkeypatch.setattr(wt, "run_isolated_task", always_red)
    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_parallel"]
    await tool.handler({"tasks": ["x"]})
    assert state["n"] == 3                                   # env 把尝试次数调到 3


@pytest.mark.asyncio
async def test_dev_isolated_retries_on_noop_then_lands(monkeypatch, tmp_path):
    # 真实历练发现的"无改动不重试"缺口：子 agent 第一次 no-op（没改文件、diff 空、测试碰巧绿）
    # → dev_isolated 自动换全新 worktree、用更命令式描述重试 → 第二次真改了 → 落分支。
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools
    state = {"n": 0, "descs": []}

    async def flaky(repo, wid, desc, build, test_cmd=None):
        state["n"] += 1
        state["descs"].append(desc)
        if state["n"] == 1:
            return ("", "啥也没干", {"ok": True, "output": ""})                 # no-op：绿但无 diff
        return ("diff --git a/f b/f\n+x\n", "改了", {"ok": True, "output": ""})  # 重试后真改
    monkeypatch.setattr(wt, "run_isolated_task", flaky)
    monkeypatch.setattr(wt, "apply_diff_to_branch",
                        lambda repo, br, diff, msg: {"ok": True, "branch": br, "error": ""})

    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_isolated"]
    out = await tool.handler({"description": "加个函数"})
    assert state["n"] == 2                                       # no-op → 自动重试一次
    assert "没有产生任何改动" in state["descs"][1]               # 第二次用了更命令式的 no-op 提示
    assert "✅" in out and "vorto/" in out and "自修复 1 次后" in out   # 重试后落分支、标了次数


@pytest.mark.asyncio
async def test_dev_isolated_all_noop_reports_clearly(monkeypatch, tmp_path):
    # 全程 no-op（始终没改文件）→ 不谎报，清楚说"未产生任何改动"并建议把描述写具体
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools
    state = {"n": 0}

    async def always_noop(repo, wid, desc, build, test_cmd=None):
        state["n"] += 1
        return ("", "没干", {"ok": True, "output": ""})
    monkeypatch.setattr(wt, "run_isolated_task", always_noop)

    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_isolated"]
    out = await tool.handler({"description": "做点啥"})
    assert state["n"] == 2                                       # 默认尝试 2 次后放弃
    assert "未产生任何改动" in out and "更具体" in out


@pytest.mark.asyncio
async def test_dev_auto_dependent_self_repairs(monkeypatch, tmp_path):
    # dev_auto 依赖接力：某依赖子任务第一次红 → 带失败反馈自修复重试 → 第二次绿
    import src.agents.worktree as wt
    import src.agents.decompose as dec
    from src.agents.main_agent import build_dev_tools
    from src.orchestrator.task_analyzer import SubTask
    a = SubTask(id="a", title="A")
    b = SubTask(id="b", title="B", dependencies=["a"])

    async def fake_decompose(task, **k):
        return {"descriptions": ["实现 A"], "independent": [a], "deferred": [b], "total": 2}
    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)

    async def fake_isolated(repo, wid, desc, build, test_cmd=None):
        return ("d\n", "c", {"ok": True, "output": ""})
    monkeypatch.setattr(wt, "run_isolated_task", fake_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: {"ok": True, "branch": br,
                                                          "applied": [m for _d, m in items], "failed": [], "integration": None})
    state = {"n": 0, "descs": []}

    async def flaky_dep(repo, wid, branch, desc, build, msg, test_cmd=None):
        state["n"] += 1
        state["descs"].append(desc)
        if state["n"] == 1:
            return {"ok": False, "conclusion": "c", "output": "DEP_FAILED_ONCE"}
        return {"ok": True, "conclusion": "c", "output": ""}
    monkeypatch.setattr(wt, "run_dependent_on_branch", flaky_dep)
    monkeypatch.setattr(wt, "verify_branch", lambda repo, br, tc, wid: {"ok": True, "output": "", "cmd": "p"})

    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_auto"]
    out = await tool.handler({"task": "big"})
    assert state["n"] == 2                                   # 依赖红 → 自修复重试
    assert "DEP_FAILED_ONCE" in state["descs"][1]           # 第二次带失败反馈
    assert "✅ 已接力提交（修复 1 次后）" in out


# ---- 流水线进度可观测（on_progress）：长任务边跑边播，免得对着静默 prompt 干等 ----

@pytest.mark.asyncio
async def test_dev_parallel_emits_progress(monkeypatch, tmp_path):
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools

    async def ok_isolated(repo, wid, desc, build, test_cmd=None):
        return ("diff\n", "c", {"ok": True, "output": ""})
    monkeypatch.setattr(wt, "run_isolated_task", ok_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: {"ok": True, "branch": br,
                                                          "applied": [m for _d, m in items], "failed": [],
                                                          "integration": {"ok": True, "output": "", "cmd": "p"}})
    events = []
    tool = {t.name: t for t in build_dev_tools(str(tmp_path), on_progress=events.append)}["dev_parallel"]
    await tool.handler({"tasks": ["甲", "乙"]})
    joined = "\n".join(events)
    assert "并行隔离实现 2 个子任务" in joined                # 开工进度
    assert "落分支" in joined and "集成测试" in joined        # 落分支 + 集成验证进度


@pytest.mark.asyncio
async def test_dev_auto_emits_progress_through_phases(monkeypatch, tmp_path):
    import src.agents.worktree as wt
    import src.agents.decompose as dec
    from src.agents.main_agent import build_dev_tools
    from src.orchestrator.task_analyzer import SubTask
    a = SubTask(id="a", title="A")
    b = SubTask(id="b", title="B", dependencies=["a"])

    async def fake_decompose(task, **k):
        return {"descriptions": ["实现 A"], "independent": [a], "deferred": [b], "total": 2}
    monkeypatch.setattr(dec, "decompose_for_parallel", fake_decompose)

    async def ok_isolated(repo, wid, desc, build, test_cmd=None):
        return ("d\n", "c", {"ok": True, "output": ""})
    monkeypatch.setattr(wt, "run_isolated_task", ok_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: {"ok": True, "branch": br,
                                                          "applied": [m for _d, m in items], "failed": [], "integration": None})

    async def ok_dep(repo, wid, branch, desc, build, msg, test_cmd=None):
        return {"ok": True, "conclusion": "c", "output": ""}
    monkeypatch.setattr(wt, "run_dependent_on_branch", ok_dep)
    monkeypatch.setattr(wt, "verify_branch", lambda repo, br, tc, wid: {"ok": True, "output": "", "cmd": "p"})

    events = []
    tool = {t.name: t for t in build_dev_tools(str(tmp_path), on_progress=events.append)}["dev_auto"]
    await tool.handler({"task": "big"})
    joined = "\n".join(events)
    assert "分解任务" in joined                              # 分解阶段
    assert "并行隔离实现" in joined                          # 独立批
    assert "依赖接力实现「B」" in joined                     # 依赖接力（带子任务名）
    assert "最终集成测试" in joined                          # 最终集成验证


@pytest.mark.asyncio
async def test_progress_callback_errors_dont_break_pipeline(monkeypatch, tmp_path):
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools

    async def ok_isolated(repo, wid, desc, build, test_cmd=None):
        return ("d\n", "c", {"ok": True, "output": ""})
    monkeypatch.setattr(wt, "run_isolated_task", ok_isolated)
    monkeypatch.setattr(wt, "apply_diffs_to_branch",
                        lambda repo, br, items, tc=None: {"ok": True, "branch": br,
                                                          "applied": [m for _d, m in items], "failed": [],
                                                          "integration": {"ok": True, "output": "", "cmd": "p"}})

    def boom(_m):
        raise RuntimeError("progress sink down")
    tool = {t.name: t for t in build_dev_tools(str(tmp_path), on_progress=boom)}["dev_parallel"]
    out = await tool.handler({"tasks": ["x"]})              # 进度回调抛错 → best-effort 吞掉，不影响流水线
    assert "1 通过测试" in out


@pytest.mark.asyncio
async def test_build_test_tool_lets_subagent_self_check(tmp_path):
    import sys
    from src.agents.main_agent import build_test_tool
    # 给隔离实现子 agent 的受限 run_tests：只跑测试、不是任意 shell
    t = build_test_tool(str(tmp_path), [sys.executable, "-c", "import sys; sys.exit(0)"])
    assert t.name == "run_tests" and t.read_only is True
    assert "通过" in await t.handler({})
    t2 = build_test_tool(str(tmp_path), [sys.executable, "-c", "import sys; sys.stderr.write('BOOM'); sys.exit(1)"])
    out = await t2.handler({})
    assert "未过" in out and "BOOM" in out                     # 失败带输出，子 agent 据此改


def test_run_tests_pass_and_fail(tmp_path):
    import sys
    from src.agents.worktree import run_tests
    assert run_tests(tmp_path, [sys.executable, "-c", "print('hi')"])["ok"] is True
    r = run_tests(tmp_path, [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(1)"])
    assert r["ok"] is False and "boom" in r["output"]


@pytest.mark.asyncio
async def test_run_isolated_task_verifies_in_worktree(tmp_path):
    import sys
    _init_repo(tmp_path)

    class WriteAgent:
        def __init__(self, root):
            self.root = root

        async def run_turn(self, desc, mode, emit):
            from pathlib import Path
            (Path(self.root) / "m.py").write_text("ok\n")
            return "done"

    # 测试通过 → ok True
    _d, _c, ver = await worktree.run_isolated_task(
        str(tmp_path), "wt-vp", "x", lambda r: WriteAgent(r),
        test_cmd=[sys.executable, "-c", "import sys; sys.exit(0)"])
    assert ver and ver["ok"] is True
    # 测试失败 → ok False + 失败输出（供 agent 据此重试）
    _d, _c, ver2 = await worktree.run_isolated_task(
        str(tmp_path), "wt-vf", "x", lambda r: WriteAgent(r),
        test_cmd=[sys.executable, "-c", "import sys; sys.stderr.write('XFAIL'); sys.exit(1)"])
    assert ver2["ok"] is False and "XFAIL" in ver2["output"]
