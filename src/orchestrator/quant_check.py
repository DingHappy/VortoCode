"""量化环境检查

检查所有依赖和配置是否就绪，给出修复建议。
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List, Tuple


class _C:
    OK = "\033[32m"
    WARN = "\033[33m"
    FAIL = "\033[31m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def _check(name: str, ok: bool, detail: str, fix: str = "") -> Tuple[str, bool, str, str]:
    return (name, ok, detail, fix)


async def check_environment() -> bool:
    """检查量化环境，返回 True 表示全部就绪。"""
    C = _C
    checks: List[Tuple[str, bool, str, str]] = []

    # 1. Python 版本
    v = sys.version_info
    checks.append(_check(
        "Python 版本", v >= (3, 10),
        f"{v.major}.{v.minor}.{v.micro}",
        "需要 Python >= 3.10",
    ))

    # 2. OPENAI_API_KEY
    api_key = os.getenv("OPENAI_API_KEY", "")
    checks.append(_check(
        "OPENAI_API_KEY", bool(api_key),
        f"已设置 ({api_key[:8]}...)" if api_key else "未设置",
        "在 .env 文件中设置 OPENAI_API_KEY=sk-xxx",
    ))

    # 3. OPENAI_API_BASE
    api_base = os.getenv("OPENAI_API_BASE", "")
    checks.append(_check(
        "OPENAI_API_BASE", bool(api_base),
        api_base if api_base else "未设置（使用默认）",
        "在 .env 中设置 OPENAI_API_BASE=https://your-gateway/v1",
    ))

    # 4. openai 库
    try:
        import openai
        checks.append(_check("openai 库", True, f"v{openai.__version__}"))
    except ImportError:
        checks.append(_check("openai 库", False, "未安装", "pip install openai"))

    # 5. akshare
    try:
        import akshare
        ver = getattr(akshare, "__version__", "unknown")
        checks.append(_check("akshare 库", True, f"v{ver}"))
    except ImportError:
        checks.append(_check("akshare 库", False, "未安装", "pip install akshare"))

    # 6. aiohttp
    try:
        import aiohttp
        checks.append(_check("aiohttp 库", True, f"v{aiohttp.__version__}"))
    except ImportError:
        checks.append(_check("aiohttp 库", False, "未安装", "pip install aiohttp"))

    # 7. quant-platform 连通性
    try:
        import aiohttp as _aiohttp
        async with _aiohttp.ClientSession(timeout=_aiohttp.ClientTimeout(total=3)) as s:
            async with s.get("http://localhost:8000/api/system/info") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    checks.append(_check(
                        "quant-platform", True,
                        f"可达 (v{data.get('version', '?')})",
                    ))
                else:
                    checks.append(_check(
                        "quant-platform", False, f"HTTP {resp.status}",
                        "cd quant-platform/python && python quant.py api --port 8000",
                    ))
    except Exception as e:
        checks.append(_check(
            "quant-platform", False, f"不可达 ({type(e).__name__})",
            "cd quant-platform/python && python quant.py api --port 8000",
        ))

    # 8. 配置文件
    for cfg_file in ("config/quant.yaml", "config/mcp.yaml"):
        exists = Path(cfg_file).exists()
        checks.append(_check(cfg_file, exists, "存在" if exists else "缺失", f"创建 {cfg_file}"))

    # 9. 钉钉配置
    try:
        import yaml
        cfg = yaml.safe_load(Path("config/quant.yaml").read_text())
        webhook = cfg.get("quant", {}).get("notifications", {}).get("dingtalk", {}).get("webhook", "")
        checks.append(_check(
            "钉钉 webhook", bool(webhook),
            f"已配置 ({webhook[:30]}...)" if webhook else "未配置（可选）",
            "在 config/quant.yaml 中填写 notifications.dingtalk.webhook",
        ))
    except Exception:
        checks.append(_check("钉钉 webhook", False, "配置读取失败", ""))

    # 10. 记忆存储目录
    mem_dir = Path(".vortocode/memory")
    mem_dir.mkdir(parents=True, exist_ok=True)
    checks.append(_check("记忆目录", True, str(mem_dir)))

    # 11. MCP 工具服务器脚本
    mcp_script = Path("src/tools/quant_mcp_server.py")
    checks.append(_check("MCP 工具服务器", mcp_script.exists(), str(mcp_script)))

    # ---- 输出 ----
    print(f"\n{C.BOLD}{'=' * 60}{C.RESET}")
    print(f"{C.BOLD}  量化环境检查{C.RESET}")
    print(f"{C.BOLD}{'=' * 60}{C.RESET}\n")

    all_ok = True
    for name, ok, detail, fix in checks:
        icon = f"{C.OK}✓{C.RESET}" if ok else f"{C.FAIL}✗{C.RESET}"
        print(f"  {icon} {name:20s}  {C.DIM}{detail}{C.RESET}")
        if not ok:
            all_ok = False
            if fix:
                print(f"    {C.WARN}→ {fix}{C.RESET}")

    print(f"\n{C.BOLD}{'=' * 60}{C.RESET}")
    if all_ok:
        print(f"  {C.OK}{C.BOLD}全部就绪！可以运行:{C.RESET}")
        print(f"    python main.py quant --cli")
        print(f"    python main.py quant --stage news")
    else:
        failed = [c for c in checks if not c[1]]
        print(f"  {C.WARN}{C.BOLD}{len(failed)} 项未通过，请修复后重试{C.RESET}")
    print(f"{C.BOLD}{'=' * 60}{C.RESET}\n")

    return all_ok
