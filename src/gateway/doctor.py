"""`vc doctor`——常驻化后的一键自检（b3 PR-6，plan-2026-07 D1 运维借鉴项）。

常驻/attach 化之后，"用不了"大多是**管道问题**（serve 没起/凭证没配/LLM 接口断了/gh 没登录），
不是代码问题。doctor 把这些逐项查清、一屏给结论，省得逐个猜。

每项检查独立 try/except（一项炸不拖全体）、网络项带短超时（不挂死）。
级别：ok（✅）/ warn（⚠️ 可用但缺配置，如 serve 没起——attach 会回退进程内）/ fail（⛔ 硬伤）。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

_TIMEOUT = 5.0


@dataclass
class Check:
    name: str
    level: str      # ok | warn | fail
    detail: str

    @property
    def glyph(self) -> str:
        return {"ok": "✅", "warn": "⚠️", "fail": "⛔"}.get(self.level, "?")


def _check_git(cwd: str) -> Check:
    import subprocess
    if not shutil.which("git"):
        return Check("git", "fail", "git 不在 PATH——一切流水线的地基")
    r = subprocess.run(["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return Check("git", "warn", "当前目录不是 git 仓库（dev 流水线需要仓库）")
    return Check("git", "ok", "git 可用，当前目录在仓库内")


def _check_api_key() -> Check:
    import src.llm.client  # noqa: F401 —— 导入即加载 .env
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return Check("api-key", "fail",
                     "OPENAI_API_KEY 未配置（.env 或环境变量）——无法对话/跑流水线")
    return Check("api-key", "ok", f"OPENAI_API_KEY 已配置（…{key[-4:]}）")


async def _check_relay() -> Check:
    """LLM 接口连通：GET /models（带 key、短超时）。不烧 token，只验网络+鉴权。"""
    import aiohttp
    from src.llm.client import DEFAULT_OPENAI_BASE_URL
    base = os.getenv("OPENAI_API_BASE", DEFAULT_OPENAI_BASE_URL).rstrip("/")
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return Check("relay", "warn", f"跳过（无 key）：{base}")
    try:
        # trust_env=True：吃 HTTP(S)_PROXY——用户经代理出网时（LLM SDK/httpx 默认吃），
        # aiohttp 默认直连会误报"LLM 接口不可达"（真机 doctor 首跑就踩了这个）。
        async with aiohttp.ClientSession(trust_env=True) as s:
            async with s.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"},
                             timeout=aiohttp.ClientTimeout(total=_TIMEOUT)) as r:
                if r.status == 200:
                    return Check("relay", "ok", f"LLM 接口可达且鉴权通过：{base}")
                if r.status in (401, 403):
                    return Check("relay", "fail", f"LLM 接口可达但鉴权被拒（HTTP {r.status}）：{base}")
                return Check("relay", "warn", f"LLM 接口响应异常（HTTP {r.status}）：{base}")
    except Exception as e:  # noqa: BLE001
        return Check("relay", "fail", f"LLM 接口不可达：{base}（{type(e).__name__}）")


async def _check_serve() -> Check:
    """serve 可达性：attach 模式的前提；不在只算 warn（CLI/TUI 会自动回退进程内）。"""
    import aiohttp
    url = (os.getenv("VORTOCODE_SERVE_URL") or "http://127.0.0.1:8080").rstrip("/")
    try:
        # 本地检查**刻意不吃代理环境**（默认 trust_env=False）：NO_PROXY 没配时，
        # 127.0.0.1 走代理会被误路由、把在跑的 serve 误报成不可达。
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{url}/api/health/quick",
                             timeout=aiohttp.ClientTimeout(total=_TIMEOUT)) as r:
                body = {}
                if r.status == 200:
                    try:
                        body = await r.json()
                    except Exception:  # noqa: BLE001
                        body = {}
                if r.status == 200 and body.get("status") == "healthy":
                    return Check("serve", "ok", f"serve 可达：{url}（--attach 可用）")
                return Check("serve", "warn",
                             f"{url} 有服务但不像 VortoCode serve（HTTP {r.status}）——"
                             f"--attach 前确认端口/URL")
    except Exception:  # noqa: BLE001
        return Check("serve", "warn",
                     f"serve 未起：{url}（--attach 会回退进程内；`vc server` 可启）")


def _check_permissions(cwd: str) -> Check:
    """permissions.yaml 严格解析。**不走** load_permissions——它为运行时安全吞解析错误、
    坏文件静默降级成空权限（fail-safe 对运行时正确，对自检就是漏报：doctor 的职责恰恰是
    把这种静默降级暴露出来，评审 #142）。"""
    p = Path(cwd) / ".vortocode" / "permissions.yaml"
    if not p.is_file():
        return Check("permissions", "ok", "无 permissions.yaml（默认权限面）")
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return Check("permissions", "fail",
                     f"permissions.yaml 解析失败（运行时会静默降级成空权限！）：{e}")
    if data is not None and not isinstance(data, dict):
        return Check("permissions", "fail",
                     f"permissions.yaml 结构不对（应为映射，实为 {type(data).__name__}）"
                     f"——运行时会静默降级成空权限")
    return Check("permissions", "ok", "permissions.yaml 可解析")


def _check_gh() -> Check:
    import subprocess
    if not shutil.which("gh"):
        return Check("gh", "warn", "gh CLI 未装——open_pr/pr_feedback 不可用（其余照常）")
    r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return Check("gh", "warn", "gh 未登录（gh auth login）——open_pr/pr_feedback 不可用")
    return Check("gh", "ok", "gh 已登录，PR 往返可用")


def _check_im() -> Check:
    """IM 配置（信息性）：没配不算问题，配了报通道。"""
    from src.gateway.im_service import IMConfigError, build_adapter
    for ch in ("telegram", "dingtalk"):
        try:
            build_adapter(ch)
            return Check("im", "ok", f"{ch} 凭证已配（`vc server --im {ch}` 可内嵌）")
        except IMConfigError:
            continue
    return Check("im", "warn", "IM 凭证未配（可选）——配了才能把 agent 搬上手机")


async def run_checks(cwd: str) -> List[Check]:
    """跑全部自检，顺序稳定（供人读与测试断言）；单项意外炸 → 记 fail，不拖全体。"""
    import inspect
    plan = [
        ("git", lambda: _check_git(cwd)),
        ("api-key", _check_api_key),
        ("relay", _check_relay),
        ("serve", _check_serve),
        ("permissions", lambda: _check_permissions(cwd)),
        ("gh", _check_gh),
        ("im", _check_im),
    ]
    out: List[Check] = []
    for name, fn in plan:
        try:
            r = fn()
            out.append(await r if inspect.isawaitable(r) else r)
        except Exception as e:  # noqa: BLE001 —— 自检工具自己不许崩
            out.append(Check(name, "fail", f"检查本身出错：{type(e).__name__}: {e}"))
    return out


def summarize(checks: List[Check]) -> tuple:
    """(展示文本, 退出码)：有 fail → 1，否则 0（warn 不算失败——可用但缺配置）。"""
    lines = [f"  {c.glyph} {c.name:<12} {c.detail}" for c in checks]
    fails = sum(1 for c in checks if c.level == "fail")
    warns = sum(1 for c in checks if c.level == "warn")
    tail = f"\n  {'⛔ ' + str(fails) + ' 项硬伤' if fails else '✓ 无硬伤'}" \
           + (f" · ⚠️ {warns} 项待配置" if warns else "")
    return "\n".join(lines) + tail, (1 if fails else 0)
