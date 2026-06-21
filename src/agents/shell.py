"""通用命令执行——让主 agent 能跑任意 shell 命令（测试/lint/git/构建/安装…）。

VortoCode 此前只有专用工具（read/edit/dev_*），缺一个通用 shell，这是对齐 Claude Code /
opencode 的核心缺口。**高危/外向**：调用方务必门控（build + 人工确认）。本模块只负责
执行 + 基本危险拦截（defense-in-depth，非完整沙箱；真正的关口是"人看着命令点确认"）。
"""
from __future__ import annotations

import re
import subprocess

# 明显灾难性的操作：即便在"始终允许"下也硬拒，作为兜底（非穷举）
_DANGER = [
    (r"\brm\s+-[a-z]*r[a-z]*f?\s+(/\s|/$|~|\$HOME|\*)", "rm -rf 根/家目录/通配"),
    (r"\b(mkfs|fdisk)\b", "磁盘格式化"),
    (r"\bdd\b.+\bof=/dev/", "dd 写裸设备"),
    (r">\s*/dev/sd", "写裸设备"),
    (r":\s*\(\)\s*\{.*:\s*\|\s*:.*\}", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "关机/重启"),
    (r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)", "git 强推"),
]


def is_dangerous(cmd: str) -> str:
    """命中明显危险操作则返回原因（非空），否则空串。"""
    for pat, why in _DANGER:
        if re.search(pat, cmd):
            return why
    return ""


def run_command(repo_root, cmd: str, timeout: int = 300) -> dict:
    """在 repo_root 跑 shell 命令，返回 {ok, code, output}。输出截尾、超时/异常兜底。"""
    try:
        r = subprocess.run(cmd, shell=True, cwd=str(repo_root),
                           capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "code": r.returncode, "output": (r.stdout + r.stderr)[-8000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "output": f"命令超时（>{timeout}s）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": -1, "output": f"无法执行: {e}"}
