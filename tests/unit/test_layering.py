"""分层契约：下层不依赖前端；宿主机命令执行开关全仓只有一份。"""
from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest
from fastapi import HTTPException

SRC = Path(__file__).resolve().parents[2] / "src"
FRONTENDS = {"web", "im"}          # 前端/传输层：可以依赖下层，反之不行
ENTRYPOINTS = {"cli.py"}                  # 命令行入口负责把各端装起来


def _imports(path, root=SRC):
    rel = path.relative_to(root).with_suffix('')
    package = '.'.join(('src', *rel.parts[:-1]))
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            if node.level:
                module = resolve_name('.' * node.level + module, package)
            yield node.lineno, module
            for alias in node.names:
                yield node.lineno, module + '.' + alias.name


@pytest.mark.parametrize('module', ['agents/gate.py', 'agents/taint.py', 'agents/capabilities.py',
                                    'memory/write_policy.py', 'sandbox/policy.py',
                                    'agents/tool.py', 'agents/tool_runtime.py',
                                    'agents/backends/control.py', 'agents/backends/codex.py',
                                    'agents/backends/execution.py', 'agents/backends/observation_order.py'])
def test_security_core_never_imports_upper_layers(module):
    forbidden = tuple('src.' + name for name in ('workflow', 'gateway', 'scheduling', 'web', 'tui', 'im'))
    found = [(line, name) for line, name in _imports(SRC / module)
             if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)]
    assert not found, f'{module}: {found}'


def test_import_walker_catches_delayed_alias_and_relative_imports(tmp_path):
    path = tmp_path / 'agents/probe.py'; path.parent.mkdir()
    path.write_text('def delayed():\n    from src import workflow as wf\n    import src.gateway as gw\n    from .. import scheduling\n    from ..web import router\n')
    names = {name for _, name in _imports(path, tmp_path)}
    assert {'src.workflow', 'src.gateway', 'src.scheduling', 'src.web'} <= names


def test_lower_layers_never_import_web_or_tui():
    """含函数内的延迟 import——那也是依赖，只是推迟到运行时才暴露。"""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        if rel.parts[0] in FRONTENDS or rel.as_posix() in ENTRYPOINTS:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            found += [f"src/{rel}:{node.lineno} {m}" for m in modules
                      if m.split(".")[:2] in (["src", "web"], ["src", "tui"])]
    assert found == []


_CRON = """\
jobs:
  - name: probe
    schedule: "at 02:00"
    command: "/bin/echo hi"
    enabled: true
"""


@pytest.mark.parametrize("allowed", [False, True])
async def test_every_host_exec_gate_follows_the_single_switch(tmp_path, monkeypatch, allowed):
    """真实现探针：只替换 sandbox.policy.shell_enabled，三个调用方必须一起变。

    哪一个没跟着变，就说明它还在读自己的那份环境变量——第二份安全闸又长回来了。
    """
    from src.sandbox import policy
    from src.sandbox import runner
    from src.scheduling.cron import check_trigger
    from src.web.auth import require_shell

    # 环境变量刻意设成与探针相反：谁还在自己读 env，结果就会和探针对不上
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "" if allowed else "1")
    monkeypatch.setattr(policy, "shell_enabled", lambda: allowed)

    # ① Web 的执行端点闸
    if allowed:
        require_shell()
    else:
        with pytest.raises(HTTPException):
            require_shell()

    # ② cron command 作业的手动触发守卫
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "cron.yaml").write_text(_CRON, encoding="utf-8")
    assert (check_trigger(str(tmp_path), "probe")[1] == "shell_disabled") is (not allowed)

    # ③ 沙箱显式 off 后回落宿主机执行
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    result = await runner.run_code("print('x')", "python")
    assert (result.runtime == "host") is allowed


def test_security_core_import_does_not_load_unrelated_subsystems():
    """真导入（不是 AST）：包级 __init__ 的再导出会让安全内核在运行时连带加载 hooks/向量记忆/
    Docker 沙箱——AST 契约看不到这条路径。在干净子进程里导入后核对 sys.modules。"""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    code = ("import sys\n"
            "import src.agents.gate, src.agents.taint, src.agents.capabilities\n"
            "import src.sandbox.policy, src.memory.write_policy\n"
            "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('src.'))))\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True,
                         text=True, check=True).stdout.split()
    unrelated = ("src.hooks", "src.skills", "src.tools", "src.memory.vector_memory",
                 "src.sandbox.docker_sandbox", "src.workflow", "src.gateway", "src.web")
    assert [m for m in out if m.startswith(unrelated)] == []
