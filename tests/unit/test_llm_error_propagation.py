"""LLM 通道故障传播（真机复盘 2026-07-24）：502 曾被 run_turn 吞成空串，流水线把"通道外伤"
误判成"子 agent no-op"，烧光重试后报"无改动/出错"——死因全程不可见。

钉死三层修复：
1. `raise_llm_errors=True`（流水线子 agent 档）下 run_turn 对瞬时 LLM 错误**抛异常**，
   不再"emit 提示 + 返回空串"；默认档（交互）行为不变。
2. `run_isolated_task` 空 diff 短路：不再为"绿测试 + 空 diff"这种永不成功的组合烧全量测试。
3. 死因透传：dev_isolated / 依赖接力把"LLM 通道故障"如实报出，而不是笼统的"无改动/出错"。
"""
import os
import subprocess

import pytest

from src.agents import worktree
from src.agents.main_agent import MainAgent, build_dev_tools


class FailingLLM:
    """每次 chat 都抛瞬时错误（模拟中转站 502/超时）。"""

    def __init__(self, exc=None):
        self.exc = exc or TimeoutError("mock 502 Bad Gateway")
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        raise self.exc


def _init_repo(path):
    def git(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (path / "a.txt").write_text("hello\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


# ---------------------------------------------------------------- run_turn 两档行为

async def test_strict_mode_raises_on_llm_failure_prompt_path():
    agent = MainAgent([], llm=FailingLLM(), raise_llm_errors=True)
    with pytest.raises(TimeoutError):
        await agent.run_turn("改一下 README")


async def test_strict_mode_raises_on_llm_failure_native_path():
    agent = MainAgent([], llm=FailingLLM(), native=True, raise_llm_errors=True)
    with pytest.raises(TimeoutError):
        await agent.run_turn("改一下 README")


async def test_default_mode_keeps_soft_empty_return():
    """交互档（默认）行为回归保护：出错仍是 emit 提示 + 返回空串，不抛。"""
    emitted = []
    agent = MainAgent([], llm=FailingLLM())
    out = await agent.run_turn("你好", emit=emitted.append)
    assert out == ""
    assert any("对话出错" in m for m in emitted)


async def test_default_mode_native_soft_return():
    emitted = []
    agent = MainAgent([], llm=FailingLLM(), native=True)
    out = await agent.run_turn("你好", emit=emitted.append)
    assert out == ""
    assert any("模型服务暂时无响应" in m for m in emitted)


# ---------------------------------------------------------------- 空 diff 短路 + 异常传播

async def test_run_isolated_task_skips_tests_on_empty_diff(tmp_path, monkeypatch):
    _init_repo(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("空 diff 不该跑测试")
    monkeypatch.setattr(worktree, "run_tests", _boom)

    class NoopAgent:
        async def run_turn(self, *a, **k):
            return "看过了，没改"

    diff, _c, ver = await worktree.run_isolated_task(
        str(tmp_path), "wt-noop", "desc", lambda p: NoopAgent(),
        test_cmd=["python3", "-c", "print(1)"])
    assert not diff.strip()
    assert ver is None                       # 没跑（短路），而不是跑了个假绿


async def test_run_isolated_task_propagates_strict_error_and_cleans_up(tmp_path):
    _init_repo(tmp_path)

    class RaisingAgent:
        async def run_turn(self, *a, **k):
            raise TimeoutError("mock 502")

    with pytest.raises(TimeoutError):
        await worktree.run_isolated_task(
            str(tmp_path), "wt-raise", "desc", lambda p: RaisingAgent())
    assert not (tmp_path / ".vortocode" / "worktrees" / "wt-raise").exists()  # finally 清理不受影响


# ---------------------------------------------------------------- 死因透传

async def test_dev_isolated_reports_channel_error(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "1")

    async def _fail(*a, **k):
        raise TimeoutError("mock 502 Bad Gateway")
    monkeypatch.setattr(worktree, "run_isolated_task", _fail)

    tools = {t.name: t for t in build_dev_tools(str(tmp_path))}
    out = await tools["dev_isolated"].handler({"description": "在 README 加一行"})
    assert "LLM 通道故障" in out
    assert "mock 502" in out
    assert "没真正修改文件" not in out       # 别再把通道外伤记成 agent 偷懒


async def test_dependent_branch_reports_channel_error(tmp_path):
    _init_repo(tmp_path)
    worktree.ensure_branch(str(tmp_path), "vorto/dep-test")

    class RaisingAgent:
        async def run_turn(self, *a, **k):
            raise TimeoutError("mock 502")

    res = await worktree.run_dependent_on_branch(
        str(tmp_path), "wt-dep", "vorto/dep-test", "desc",
        lambda p: RaisingAgent(), "msg")
    assert res["ok"] is False
    assert "LLM 通道故障" in res["output"]


async def test_dependent_branch_skips_tests_when_no_changes(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    worktree.ensure_branch(str(tmp_path), "vorto/dep-noop")

    def _boom(*a, **k):
        raise AssertionError("无改动不该跑测试")
    monkeypatch.setattr(worktree, "run_tests", _boom)

    class NoopAgent:
        async def run_turn(self, *a, **k):
            return "没改"

    res = await worktree.run_dependent_on_branch(
        str(tmp_path), "wt-dep2", "vorto/dep-noop", "desc",
        lambda p: NoopAgent(), "msg", test_cmd=["python3", "-c", "print(1)"])
    assert res["ok"] is False
    assert "无改动" in res["output"]


# ---------------------------------------------------------------- IM 交互路径的死因兜底

async def test_im_chat_surfaces_error_instead_of_empty(tmp_path):
    """真机症状：LLM 502 时 IM 只回一句"（无输出）"——emit 里的死因被丢掉了。

    现在：返回值为空且 emit 有内容时，把 emit 的错误原文交出去。
    """
    from src.im.bridge import IMBridge

    class _Adapter:
        edits_supported = False

        async def poll(self):                      # pragma: no cover
            return
            yield

        async def send_text(self, text):
            sent.append(text)
            return ""

        async def edit_text(self, mid, text):
            sent.append(text)

        async def send_confirm(self, text, cid):   # pragma: no cover
            sent.append(text)

        async def ack_callback(self, ev):          # pragma: no cover
            return

        async def close(self):                     # pragma: no cover
            return

    sent: list = []
    bridge = IMBridge(str(tmp_path), _Adapter(), "owner-1", channel="dingtalk")

    class _FailingAgent:
        async def run_turn(self, text, mode="plan", say=None, emit=None, **kw):
            if emit:
                emit("对话出错: APIStatusError: 502 Bad Gateway（多为中转站/网络/额度问题）")
            return ""                              # 老代码就是在这里把死因丢了

    bridge.agent = _FailingAgent()
    await bridge._run_turn("随便问点什么")

    body = "\n".join(sent)
    assert "502" in body, f"死因没透出来：{sent}"
    assert "（无输出）" not in body


# ---------------------------------------------------------------- 沙箱标记（护城河可通行性）

def test_run_tests_marks_sandbox_env(tmp_path, monkeypatch):
    """流水线在沙箱里跑验证时必须注入 VORTOCODE_IN_SANDBOX=1。

    真机复盘 2026-07-25：沙箱按设计禁 fork PTY，4 条 terminal 用例在里面必红 →
    验证关对**任何**候选改动一律判红，隔离 dev 流水线 100% 堵死。用例靠这个标记
    如实跳过（宿主机门禁仍覆盖），标记丢了护城河就再次堵死，所以钉死它。
    """
    import subprocess as _sp
    from src.agents import worktree as wt

    seen: dict = {}

    class _Decision:
        allowed = True
        isolated = True
        backend = "seatbelt"
        reason = ""

        def to_dict(self):
            return {"backend": "seatbelt", "isolated": True}

    monkeypatch.setattr(wt, "_git", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("src.agents.sandbox.resolve_sandbox", lambda **k: _Decision())
    monkeypatch.setattr("src.agents.sandbox.sandboxed_exec_argv",
                        lambda w, c, backend=None: list(c))
    monkeypatch.setattr("src.agents.sandbox.child_env", lambda: {"PATH": "/usr/bin"})

    def _fake_run(cmd, **kw):
        seen["env"] = kw.get("env") or {}
        return _sp.CompletedProcess(cmd, 0, "ok", "")
    monkeypatch.setattr(wt.subprocess, "run", _fake_run)

    wt.run_tests(str(tmp_path), ["true"])
    assert seen["env"].get("VORTOCODE_IN_SANDBOX") == "1"


def test_sandboxed_terminal_tests_skip_themselves():
    """反向钉死：标记置位时，terminal 用例必须跳过而不是失败。"""
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/unit/test_gateway_terminals.py",
         "--collect-only", "-q"],
        capture_output=True, text=True,
        env={**os.environ, "VORTOCODE_IN_SANDBOX": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
