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
