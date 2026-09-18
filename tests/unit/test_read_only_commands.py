"""只读命令判定——授权档位 READS 要在命令面也说得通。

真机诊断（2026-09-17）：`run_command` 一律按执行面申报确认，于是 READS（"只读操作免确认"）
这一档对命令面等于不存在——`cat src/x.py` 照样弹确认框。

判定**一律 fail-closed**：判不出来就当执行面（多问一次），绝不反过来。下面的用例分两组，
第二组（"这些必须判成执行面"）才是真正的安全断言。
"""

import pytest

from src.agents.shell import is_read_only

READ_ONLY = [
    "cat src/x.py",
    "head -50 README.md",
    "tail -f logs/app.log",
    "ls -la src",
    "wc -l src/*.py",
    "grep -rn build_session src",
    "rg --files-with-matches TODO",
    "git status",
    "git log --oneline -20",
    "git diff HEAD~1",
    "git show abc123:src/x.py",
    "git rev-parse --short HEAD",
    'find . -name "*.py"',
    "grep -rn foo src | head -20",          # 每段都只读 → 整条只读
    "cat a.json | jq .name | sort | uniq",
    "sudo cat /etc/hosts",                  # 包装前缀剥掉后仍是只读
    "FOO=1 ls",                             # 环境赋值同理
]

MUST_ASK = [
    # —— 会写盘 ——
    "cat a > b",
    "echo hi >> notes.md",
    "sed -i 's/a/b/' src/x.py",             # sed 有原地改写形态，整个排除在白名单外
    "awk '{print > \"out.txt\"}' in.txt",   # awk 的 print 重定向同理
    'find . -name "*.pyc" -delete',
    "find . -name x -exec rm {} ;",
    "rm -rf build",
    "mv a b",
    "cp a b",
    "touch new.txt",
    "mkdir -p x",
    # —— git 的写形态 ——
    "git branch -D feature",
    "git checkout main",
    "git commit -m x",
    "git config --global user.name me",     # config 有写形态 → 整个子命令不收
    "git stash list",                       # stash 有写形态 → 同理，宁可多问
    # —— 能跑任意代码 ——
    "python -c 'print(1)'",
    "pytest -q",
    "npm run build",
    "make",
    "bash script.sh",
    "echo $(whoami)",                       # 命令替换
    "echo `id`",
    "cat ${HOME}/.ssh/id_rsa",              # 变量展开一律不收
    "curl https://example.com",
    "cat a.py && rm a.py",                  # 有一段不只读，整条就不只读
    "ls; rm -rf x",
    "",
    "   ",
]


@pytest.mark.parametrize("cmd", READ_ONLY)
def test_reads_are_recognized(cmd):
    assert is_read_only(cmd) is True, cmd


@pytest.mark.parametrize("cmd", MUST_ASK)
def test_anything_that_can_write_or_execute_still_asks(cmd):
    assert is_read_only(cmd) is False, cmd


async def test_run_command_declares_read_for_a_read(tmp_path):
    """端到端：读命令申报 READ，于是 READS 档下不再打扰用户。"""
    from src.agents.gate import make_confirm_gate
    from src.agents.tools.shell import build_command_tool
    from src.agents.trust import READS

    asked: list[str] = []

    async def ask(message):
        asked.append(message)
        return False                         # 拒绝：只验"问没问"，不真跑命令

    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="local",
                             trust_level=READS)
    run = next(tool for tool in build_command_tool(str(tmp_path), gate)
               if tool.name == "run_command")

    await run.handler({"command": "rm -rf build"})
    assert len(asked) == 1                   # 执行面照问

    result = await run.handler({"command": "ls -la"})
    assert len(asked) == 1, asked            # 只读的这条没再问
    assert "用户拒绝" not in result
