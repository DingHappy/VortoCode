"""Runtime 退出时不能再载入新模块。

App 更新会先替换磁盘上的打包文件、再让旧进程退出；旧进程退出时才 import 的模块会读到新包、解压失败
（真机引擎日志连续 10 次 zlib.error），之后登记的清理全部跳过。这里在全新进程里跑一遍 Runtime 生命周期，
开始服务后让所有还没载入的模块都载入失败，断言退出时每一项清理照样执行、租约照样释放。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = r"""
import asyncio, json, sys
from types import SimpleNamespace
from src.gateway.process_lease import ProcessLease
from src.gateway.runtime import runtime_lifespan
from src.utils.state_dir import state_dir

class ReplacedBundle:
    # 模拟更新后打包文件已被换掉：任何还没载入的模块都载入失败，并记下是谁
    def __init__(self):
        self.blocked = []
    def find_spec(self, name, path=None, target=None):
        self.blocked.append(name)
        raise ImportError(f"bundle replaced: {name}")

async def main(root):
    calls = []
    async def runner_shutdown():
        calls.append("runner")
    async def runs_shutdown():
        calls.append("runs")
    runner = SimpleNamespace(recover=lambda: [], shutdown=runner_shutdown)
    finder = ReplacedBundle()
    error = None
    try:
        async with runtime_lifespan(
                root, runner=runner, scheduler=None, audit_dependencies=lambda: {},
                make_run_manager=lambda: SimpleNamespace(recover=lambda: [], shutdown=runs_shutdown),
                make_terminal_manager=lambda: SimpleNamespace(shutdown=lambda: calls.append("terminals")),
                register_im_worker=lambda worker: None):
            sys.meta_path.insert(0, finder)
    except BaseException as exc:  # noqa: BLE001 - 原样报告给外层断言
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)
    lease = ProcessLease(state_dir(root) / "runtime.lock")
    try:
        lease.acquire()
        released = True
        lease.release()
    except RuntimeError:
        released = False
    print(json.dumps({"calls": calls, "blocked": finder.blocked, "error": error, "released": released}))

asyncio.run(main(sys.argv[1]))
"""


def test_shutdown_runs_every_cleanup_after_the_installed_bundle_was_replaced(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("VORTOONE_", "VORTOCODE_"))}
    env["PYTHONPATH"] = str(ROOT)
    done = subprocess.run([sys.executable, "-c", SCRIPT, str(tmp_path)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["error"] is None
    assert result["blocked"] == []          # 退出路径没有去载入任何新模块
    assert sorted(result["calls"]) == ["runner", "runs", "terminals"]
    assert result["released"] is True
