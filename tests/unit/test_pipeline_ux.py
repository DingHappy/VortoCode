"""流水线体验优化（真机复盘 2026-07-26）：验证分档、描述去重、IM 心跳。

起因：钉钉发一句「README 加一行」，端到端跑了 13 分钟——其中 8 分钟花在给纯文档改动跑两遍
全量测试（自测 + 集成），而本仓没有任何测试断言文档内容，那 8 分钟是零信号的表演；期间 IM 端
安静七八分钟，看着像死机；终报里每条子任务描述还都被打印成「X：X」。

三条红线钉死：
1. **分档只砍表演，不放松代码**：任何非文档后缀、空 diff、解析不出路径 → 一律全量。
2. **跳过必须明说**：文档档位的"绿"是没跑测试的绿，台账/结论/IM 都要写明「跳过≠通过」。
3. **心跳只在真有任务跑时说话**，且不跟真进度抢话。
"""
import asyncio
from pathlib import Path

import pytest

from src.agents import worktree
from src.agents.decompose import describe_subtask


# ---------------------------------------------------------------- 验证分档

_DOC_DIFF = """diff --git a/README.md b/README.md
index f118800..4163e98 100644
--- a/README.md
+++ b/README.md
@@ -125,6 +125,7 @@
+- **钉钉审批闭环已验证**
"""

_CODE_DIFF = """diff --git a/src/agents/gate.py b/src/agents/gate.py
index 1111111..2222222 100644
--- a/src/agents/gate.py
+++ b/src/agents/gate.py
@@ -1,3 +1,4 @@
+X = 1
"""

_MIXED_DIFF = _DOC_DIFF + _CODE_DIFF


@pytest.mark.parametrize("diff,expected", [
    (_DOC_DIFF, "docs"),
    (_CODE_DIFF, "full"),
    (_MIXED_DIFF, "full"),          # 掺一个代码文件就必须全量
    ("", "full"),                   # 空 diff：宁可多跑，不可漏
    ("随便一段不是 diff 的文本", "full"),   # 解析不出路径 → 落回全量
])
def test_verify_tier_fails_closed_toward_full(diff, expected):
    assert worktree.verify_tier(diff) == expected


def test_verify_tier_rename_into_code_is_full():
    """改名的两侧都要看：文档改名成 .py 也必须走全量。"""
    diff = "diff --git a/notes.md b/src/thing.py\nsimilarity index 100%\n"
    assert worktree.verify_tier(diff) == "full"


async def test_docs_diff_skips_tests_and_says_so(tmp_path, monkeypatch):
    """纯文档改动：不跑测试，但结果必须自带「跳过≠通过」的标注。"""
    _init_repo(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("纯文档改动不该跑测试套件")
    monkeypatch.setattr(worktree, "run_tests", _boom)

    class DocAgent:
        async def run_turn(self, *a, **k):
            return "改了 README"

    def _work(p):
        (Path(p) / "README.md").write_text("hello\n新增一行\n", encoding="utf-8")
        return DocAgent()

    diff, _c, ver = await worktree.run_isolated_task(
        str(tmp_path), "wt-doc", "desc", _work, test_cmd=["python3", "-c", "print(1)"])
    assert diff.strip()
    assert ver["ok"] is True
    assert ver["skipped"] is True
    assert "跳过 ≠ 通过" in ver["output"]        # 绝不能谎报"全绿"


async def test_code_diff_still_runs_full_tests(tmp_path, monkeypatch):
    """代码改动一律照跑全量——分档不能变成放水口。"""
    _init_repo(tmp_path)
    ran = {"n": 0}

    def _fake_run(*a, **k):
        ran["n"] += 1
        return {"ok": True, "output": "", "cmd": "pytest", "sandbox": {}}
    monkeypatch.setattr(worktree, "run_tests", _fake_run)

    class CodeAgent:
        async def run_turn(self, *a, **k):
            return "改了代码"

    def _work(p):
        (Path(p) / "mod.py").write_text("X = 1\n", encoding="utf-8")
        return CodeAgent()

    _d, _c, ver = await worktree.run_isolated_task(
        str(tmp_path), "wt-code", "desc", _work, test_cmd=["python3", "-c", "print(1)"])
    assert ran["n"] == 1
    assert not ver.get("skipped")


# ---------------------------------------------------------------- 描述去重

class _Sub:
    def __init__(self, title, description, acceptance_criteria=()):
        self.title = title
        self.description = description
        self.acceptance_criteria = list(acceptance_criteria)


def test_describe_subtask_dedupes_identical_title_and_desc():
    s = _Sub("在 README 加一行", "在 README 加一行")
    assert describe_subtask(s) == "在 README 加一行"       # 不再是 "X：X"


def test_describe_subtask_keeps_richer_side_when_prefix():
    s = _Sub("加一行", "加一行：写明钉钉闭环已验证")
    assert describe_subtask(s) == "加一行：写明钉钉闭环已验证"


def test_describe_subtask_keeps_distinct_pair():
    s = _Sub("标题", "另一段描述")
    assert describe_subtask(s) == "标题：另一段描述"


def test_describe_subtask_still_appends_acceptance():
    s = _Sub("做事", "做事", ["测试绿"])
    assert describe_subtask(s) == "做事。验收标准：测试绿"


# ---------------------------------------------------------------- IM 心跳

def _init_repo(path):
    import subprocess

    def git(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (path / "seed.txt").write_text("x\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


class _Adapter:
    edits_supported = False

    def __init__(self, sent):
        self.sent = sent

    async def poll(self):  # pragma: no cover
        return
        yield

    async def send_text(self, text):
        self.sent.append(text)
        return ""

    async def edit_text(self, mid, text):
        self.sent.append(text)

    async def send_confirm(self, text, cid):  # pragma: no cover
        self.sent.append(text)

    async def ack_callback(self, ev):  # pragma: no cover
        return

    async def close(self):  # pragma: no cover
        return


class _Task:
    def __init__(self, tid, status, log):
        self.id = tid
        self.status = status
        self.log = log


class _Runner:
    def __init__(self, tasks):
        self._tasks = tasks

    def list(self):
        return self._tasks

    def subscribe(self, cb):
        return lambda: None


async def test_heartbeat_reports_running_task_with_elapsed(tmp_path, monkeypatch):
    from src.im.bridge import IMBridge

    sent: list = []
    runner = _Runner([_Task("task-1", "running", ["🔍 集成验证中…"])])
    bridge = IMBridge(str(tmp_path), _Adapter(sent), "owner", channel="dingtalk", runner=runner)
    bridge._heartbeat_every = 0.01

    task = asyncio.create_task(bridge._heartbeat_loop())
    await asyncio.sleep(0.05)
    task.cancel()

    body = "\n".join(sent)
    assert "task-1" in body and "仍在跑" in body
    assert "集成验证" in body                       # 带上当前阶段，不是干巴巴一句"还活着"
    assert "已用" in body


async def test_heartbeat_silent_when_nothing_running(tmp_path):
    from src.im.bridge import IMBridge

    sent: list = []
    runner = _Runner([_Task("task-1", "done", ["完成"])])
    bridge = IMBridge(str(tmp_path), _Adapter(sent), "owner", channel="dingtalk", runner=runner)
    bridge._heartbeat_every = 0.01

    task = asyncio.create_task(bridge._heartbeat_loop())
    await asyncio.sleep(0.05)
    task.cancel()

    assert sent == []                                # 没任务在跑就一个字都别说


async def test_heartbeat_yields_to_real_progress(tmp_path):
    """刚播过真进度就别插嘴——避免心跳和阶段播报叠着刷屏。"""
    import time as _t
    from src.im.bridge import IMBridge

    sent: list = []
    runner = _Runner([_Task("task-1", "running", ["实现中…"])])
    bridge = IMBridge(str(tmp_path), _Adapter(sent), "owner", channel="dingtalk", runner=runner)
    bridge._heartbeat_every = 0.01                    # tick 很快
    bridge._heartbeat_quiet = 10.0                    # 但安静窗口很长
    bridge._task_prog["task-1"] = _t.monotonic()      # 假装刚刚播过真进度

    task = asyncio.create_task(bridge._heartbeat_loop())
    await asyncio.sleep(0.03)
    task.cancel()

    assert sent == []


# ---------------------------------------------------------------- 落分支失败的真实死因

def test_land_note_reports_real_git_error_not_guessed_conflict():
    """落分支失败必须报**真实 git 报错**，不许一律硬写成「与其它块文本冲突」。

    真机 2026-07-26：新机器没配 git user.name/email，`commit 失败: Author identity unknown`
    被报成「自测绿但与其它块文本冲突」——当时只有一个块，何来"与其它块冲突"？这句话把人
    引向完全错的排查方向（去找冲突），而真因是一条 git config。同一病根：**死因被改写**。
    """
    from src.agents.main_agent import land_note

    msg = "dev_auto[ind-0]: 在 README 加一行"
    status, note = land_note(msg, set(), {msg: "commit 失败: Author identity unknown"})
    assert status == "failed"
    assert "Author identity unknown" in note
    assert "文本冲突" not in note


def test_land_note_marks_landed_only_when_really_applied():
    from src.agents.main_agent import land_note

    msg = "dev_auto[ind-0]: x"
    assert land_note(msg, {msg}, {}) == ("landed", "")


def test_land_note_falls_back_to_guess_only_without_error_text():
    """拿不到真错误时才退回猜测，且措辞必须标明是猜的。"""
    from src.agents.main_agent import land_note

    msg = "dev_auto[ind-0]: x"
    status, note = land_note(msg, set(), {msg: ""})
    assert status == "failed"
    assert "疑" in note                      # 不能把猜测说成结论


def test_land_note_handles_whole_apply_failure():
    """块既没落地也没单独失败记录 → 整体落分支失败，仍要带上能拿到的原因。"""
    from src.agents.main_agent import land_note

    status, note = land_note("dev_auto[ind-0]: x", set(),
                             {"其它块": "worktree add 失败: 磁盘满"})
    assert status == "failed"
    assert "整体失败" in note and "磁盘满" in note


# ---------------------------------------------------------------- 开跑前的环境预检

def test_preflight_flags_missing_git_identity(tmp_path, monkeypatch):
    """没配 git 提交身份 → 预检必须拦住，并给出可直接粘贴的修复命令。

    真机 2026-07-26：新机器没配身份，任务照常分解、实现、自测**全绿**，最后一步 commit 才挂
    （Author identity unknown）——烧掉几十秒 LLM 和一整轮工作，撞上的却是一条 git config。
    """
    import subprocess
    from src.agents.main_agent import preflight_dev

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")   # 屏蔽全局身份，模拟新机器

    # heal=False 验的是**检测**契约（本条测试的原意）。自愈层上线后默认会就地把它修好，
    # 所以这里显式关掉自愈才测得到检测本身——两件事分开钉，别混成一条。
    problems = preflight_dev(str(tmp_path), heal=False)
    assert problems, "没配身份却放行了"
    joined = "\n".join(problems)
    assert "git config" in joined and "user.email" in joined   # 给命令，不是只说"有问题"


async def test_preflight_heals_missing_identity_instead_of_blocking(tmp_path, monkeypatch):
    """**行为变更（自愈层）**：没配身份不再是死路——就地配一个仓库级机器人身份并放行。

    原来的契约是"拦住 + 给你命令"，那已经比"跑到最后才挂"好得多，但仍然要人。
    现在的契约是"能自己修的就自己修"，且必须**说出来**（静默自愈会让人误以为环境一直是好的）。
    修的作用域限本仓库、不覆盖已有身份——那几条在 tests/unit/test_self_healing.py 里钉着。
    """
    import subprocess

    from src.agents.main_agent import preflight_dev

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))

    said: list = []
    assert preflight_dev(str(tmp_path), on_heal=said.append) == [], "自愈后仍被拦住"
    assert said and "自愈" in said[0], "修好了却没说出来"


def test_preflight_passes_with_identity(tmp_path):
    import subprocess
    from src.agents.main_agent import preflight_dev

    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    git("config", "user.name", "t")
    git("config", "user.email", "t@t")

    assert preflight_dev(str(tmp_path)) == []


async def test_dev_auto_refuses_to_start_without_identity(tmp_path, monkeypatch):
    """预检不过时 dev_auto 必须**在分解之前**返回——一次 LLM 都不许调。

    自愈层上线后这条要在**自愈关掉**（VORTOCODE_SELF_HEAL=0）时验：不变量本身没变——
    "环境不合格就别烧 token"——只是现在多了一条更好的出路（能修就修好，见下一条测试）。
    修不了的东西照旧必须拦住，那才是这条测试真正守着的东西。
    """
    import subprocess
    from src.agents import decompose as dc
    from src.agents.main_agent import build_dev_tools

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("VORTOCODE_SELF_HEAL", "0")

    async def _boom(*a, **k):
        raise AssertionError("预检没过却开始分解了——白烧 token")
    monkeypatch.setattr(dc, "decompose_for_parallel", _boom)

    tools = {t.name: t for t in build_dev_tools(str(tmp_path))}
    out = await tools["dev_auto"].handler({"task": "随便干点啥"})
    assert "预检未过" in out and "git config" in out


async def test_dev_auto_proceeds_after_healing_the_environment(tmp_path, monkeypatch):
    """自愈之后 dev_auto 不再被拦——环境被修好了，本来就不该再拦。

    断言方式：把分解函数换成一个会抛的哨兵。**抛出来说明走过了预检**（原来那条测试
    正是靠"没抛"来证明被拦住的），一正一反把这条边界钉死。
    """
    import subprocess

    from src.agents import decompose as dc
    from src.agents.main_agent import build_dev_tools

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))

    reached: list = []

    async def _sentinel(*a, **k):
        reached.append(True)
        raise RuntimeError("走到分解了")
    monkeypatch.setattr(dc, "decompose_for_parallel", _sentinel)

    tools = {t.name: t for t in build_dev_tools(str(tmp_path))}
    out = await tools["dev_auto"].handler({"task": "随便干点啥"})
    assert reached, f"自愈后仍被预检拦住：{out[:200]}"
