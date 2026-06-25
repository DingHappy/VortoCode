"""OS 级沙箱（macOS Seatbelt / sandbox-exec），给 run_command 限制爆炸半径。

run_command 此前直接 `subprocess.run(shell=True)` 在宿主上跑——只有 is_dangerous 黑名单 +
人工确认 + 细粒度权限三层兜底，**没有任何系统级隔离**。本模块在 macOS 上用 `sandbox-exec`
（Seatbelt）把命令的**文件写入限制在仓库根目录 + 临时目录**，其余（读、exec、网络）放行——
即便一条被确认放行的命令意外 `rm` 或写到家目录/系统目录，也写不出仓库去。

设计取舍：
- **opt-in**（env `VORTOCODE_SANDBOX` 真值才启用），默认关 → 现有行为零改变。auto-on 风险大
  （沙箱配错会让正常命令全失败），必须用户显式开。
- **优雅降级**：非 macOS / 无 `sandbox-exec` 二进制 → 直接放行（不沙箱、照常跑），绝不因此报错。
- **限写不限读/网**：默认只挡文件写出仓库（防 rm -rf ~ / 改系统文件），不挡 pip/git/网络，
  免得把 install/push 等正常命令一并掐死、逼用户关沙箱。
"""
from __future__ import annotations

import os
import shutil
import sys

_TEMP_WRITE_SUBPATHS = ("/private/tmp", "/tmp", "/private/var/folders", "/var/folders")
_TEMP_WRITE_LITERALS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/dtracehelper")


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "y", "on")


def sandbox_available() -> bool:
    """当前平台是否真能沙箱：仅 macOS 且 `sandbox-exec` 二进制在场。"""
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


def sandbox_enabled() -> bool:
    """是否启用沙箱：用户经 env `VORTOCODE_SANDBOX` 显式开 **且** 平台支持。两者缺一即否（放行）。"""
    return _truthy(os.getenv("VORTOCODE_SANDBOX")) and sandbox_available()


def _sbpl_quote(path: str) -> str:
    """把路径塞进 SBPL 的双引号字符串，转义反斜杠与引号（路径极少含，但稳妥）。"""
    return path.replace("\\", "\\\\").replace('"', '\\"')


def build_sandbox_profile(repo_root: str) -> str:
    """生成 SBPL（Seatbelt）profile：默认全放行，但文件写入仅限仓库根 + 临时目录。

    规则按出现顺序求值、后匹配者胜：先 `allow default`，再 `deny file-write*`（挡掉所有写），
    最后对仓库根/临时目录 `allow file-write*`（这些路径的写被重新放行）。非写操作只命中
    `allow default` → 放行。repo_root 取 realpath（macOS /var→/private/var 等符号链需归一）。
    """
    root = os.path.realpath(repo_root)
    write_subpaths = [root, *_TEMP_WRITE_SUBPATHS]
    allow_subpaths = "\n  ".join(f'(subpath "{_sbpl_quote(p)}")' for p in write_subpaths)
    allow_literals = "\n  ".join(f'(literal "{_sbpl_quote(p)}")' for p in _TEMP_WRITE_LITERALS)
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        "(allow file-write*\n"
        f"  {allow_subpaths}\n"
        f"  {allow_literals})\n"
    )


def sandboxed_argv(repo_root: str, cmd: str) -> list[str]:
    """把一条 shell 命令包成 `sandbox-exec -p <profile> /bin/sh -c <cmd>` 的 argv。

    调用方应在 sandbox_enabled() 为真时用它替代 shell=True 直跑（见 shell.run_command）。
    """
    profile = build_sandbox_profile(repo_root)
    return ["sandbox-exec", "-p", profile, "/bin/sh", "-c", cmd]
