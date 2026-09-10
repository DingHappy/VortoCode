"""套件必须与**机器自带的凭据/开关**隔开——`.env` 里有什么都不该改变测试结论。

CLAUDE.md 教人敲的是裸命令 `python -m pytest tests/ -q`。在 `.env` 里配了
`VORTOCODE_API_TOKEN` 的机器上（生产机 192.168.10.97 就是这样），改之前那条命令会红 55 条，
报错还长得像代码坏了：`KeyError: 'artifacts'`——其实是鉴权开着、返回 401，响应体里根本
没有那个键。

`ci-local.sh` 与 CI 一直是对的（它们把这些设成空串）。问题在于**保护只来自调用方**：
谁敲裸命令谁中招。这几条把保护挪进套件本身，并钉死那个反直觉的空串语义。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_AMBIENT = ("OPENAI_API_KEY", "VORTOCODE_API_TOKEN",
            "VORTOCODE_ENABLE_SHELL", "VORTOCODE_ENABLE_BROWSER")


@pytest.mark.parametrize("name", _AMBIENT)
def test_ambient_credentials_are_neutral_inside_the_suite(name):
    """无论跑在谁的机器上，测试体内看到的都该是空。"""
    assert not os.getenv(name), f"{name} 在测试里不该有值——套件被机器环境污染了"


def test_a_test_can_still_opt_into_a_value(monkeypatch):
    """夹具给的是默认不是禁令：真要这个值的用例照常覆盖得了。"""
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "explicit")
    assert os.getenv("VORTOCODE_API_TOKEN") == "explicit"


def test_empty_string_blocks_dotenv_but_deleting_the_key_does_not(tmp_path):
    """**这条钉的是那个反直觉的语义，也是我踩过的坑。**

    `load_dotenv(override=False)` 的判据是"键在不在 os.environ 里"，空串也算在。于是两种
    "清掉"的写法效果完全相反：

        VORTOCODE_API_TOKEN=""        → dotenv 不覆盖 → 真的空
        env -u VORTOCODE_API_TOKEN    → 键没了 → dotenv 从 .env 读回来 → 反而有值

    2026-09-10 我用后者去验生产机，把 55 条环境失败误判成回归。这里用**真的 dotenv**
    在临时目录上验，不打真网也不碰仓库的 .env。
    """
    pytest.importorskip("dotenv")
    env_file = tmp_path / ".env"
    env_file.write_text("PROBE_TOKEN=from-dotenv\n", encoding="utf-8")

    script = (
        "import os, sys\n"
        "from dotenv import load_dotenv\n"
        f"load_dotenv({str(env_file)!r})\n"
        "sys.stdout.write(os.getenv('PROBE_TOKEN', '<unset>'))\n"
    )

    def run(env):
        return subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, env=env, cwd=str(tmp_path)).stdout

    base = {k: v for k, v in os.environ.items() if k != "PROBE_TOKEN"}
    assert run({**base, "PROBE_TOKEN": ""}) == ""              # 空串：挡住了
    assert run(base) == "from-dotenv"                          # 键不存在：被读了回来


def test_the_gate_script_uses_the_form_that_actually_works():
    """门禁脚本里必须是 `export X=""` 而不是 `unset X`——写反了保护就没了。

    这里读的是脚本的**可执行语义**（跑一遍 shell 看变量最终是什么），不是拿正则找字符串。
    """
    gate = Path(__file__).resolve().parents[2] / "scripts" / "ci-local.sh"
    if not gate.is_file():
        pytest.skip("找不到 ci-local.sh")
    exports = [ln.strip() for ln in gate.read_text(encoding="utf-8").splitlines()
               if ln.strip().startswith("export ") and any(n in ln for n in _AMBIENT)]
    assert exports, "门禁脚本里没有清理这些变量的语句了？"
    probe = "\n".join(exports) + "\n" + "\n".join(
        f'[ -z "${{{n}+x}}" ] && echo "{n}:UNSET" || echo "{n}:SET"' for n in _AMBIENT)
    out = subprocess.run(["bash", "-c", probe], capture_output=True, text=True).stdout
    for name in _AMBIENT:
        assert f"{name}:SET" in out, f"{name} 被 unset 而不是设成空串——dotenv 会把它读回来"
