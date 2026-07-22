"""编排器起「跑模型生成/被改代码」子进程时，操作密钥不得泄漏（对抗审查 F1/F2 回归钉）。

拦截真实的 create_subprocess_exec、捕获传入的 env 参数断言——真行为测试，不查源码。
钉住的是：self-improve 的测试运行器 / code-fix 的门控都必须经 child_env()，将来有人
改回裸 os.environ（曾经就是漏 5/6、3/6 的临时擦除）会立刻变红。
"""
import pytest

from src.agents.sandbox import _STRIPPED_ENV_KEYS


class _FakeProc:
    returncode = 0

    async def communicate(self):
        return (b"", b"")


@pytest.mark.asyncio
async def test_self_improve_runner_strips_operating_secrets(monkeypatch, tmp_path):
    """自改进跑模型生成的测试内容——传给子进程的 env 不得含任一操作密钥（F1）。"""
    from src.orchestrator import self_improve as si

    for key in _STRIPPED_ENV_KEYS:
        monkeypatch.setenv(key, "leak-me")
    monkeypatch.delenv("VORTOCODE_ENV_PASSTHROUGH", raising=False)

    captured = {}

    async def fake_exec(*args, env=None, **kwargs):
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(si.asyncio, "create_subprocess_exec", fake_exec)
    loop = si.SelfImprovementLoop(repo_root=str(tmp_path))
    await loop._default_runner("test_probe.py", "def test_x():\n    assert True\n")

    assert captured["env"] is not None
    for key in _STRIPPED_ENV_KEYS:
        assert key not in captured["env"], f"{key} 泄漏进 self-improve 测试运行器"


@pytest.mark.asyncio
async def test_code_fix_gate_strips_operating_secrets(monkeypatch, tmp_path):
    """代码修复门控跑 tests/（会 import 被模型改过的模块）——env 不得含操作密钥（F2）。"""
    from src.orchestrator import code_fix as cf

    for key in _STRIPPED_ENV_KEYS:
        monkeypatch.setenv(key, "leak-me")
    monkeypatch.delenv("VORTOCODE_ENV_PASSTHROUGH", raising=False)

    captured = {}

    async def fake_exec(*args, env=None, **kwargs):
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(cf.asyncio, "create_subprocess_exec", fake_exec)
    loop = cf.CodeFixLoop(repo_root=str(tmp_path))
    await loop._default_gate()

    assert captured["env"] is not None
    for key in _STRIPPED_ENV_KEYS:
        assert key not in captured["env"], f"{key} 泄漏进 code-fix 门控"
