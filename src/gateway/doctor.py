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

from src.utils.http import local_session, outbound_session

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
    # 光有 git 和仓库还不够——**提交得了吗**才是流水线真正依赖的。真机 2026-07-26：新机器
    # 没配身份，doctor 显示绿，任务却在最后一步 commit 挂掉（Author identity unknown）。
    def _cfg(key: str) -> str:
        rr = subprocess.run(["git", "-C", cwd, "config", "--get", key],
                            capture_output=True, text=True)
        return (rr.stdout or "").strip()
    if not (_cfg("user.name") and _cfg("user.email")):
        return Check("git", "fail",
                     "git 提交身份未配置——落分支必然失败。"
                     'git config --global user.name "…" && git config --global user.email "…"')
    return Check("git", "ok", "git 可用、当前目录在仓库内、提交身份已配")


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
    from src.llm.client import DEFAULT_LLM_BASE_URL
    base = os.getenv("OPENAI_API_BASE", DEFAULT_LLM_BASE_URL).rstrip("/")
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return Check("relay", "warn", f"跳过（无 key）：{base}")
    try:
        # 与 llm/client.py 真正发请求的那条走**同一个工厂**：口径分岔的后果是体检说"可达"、
        # 真调用却直连失败——报告和事实说的不是一回事（这正是本次要根除的那类问题）。
        async with outbound_session() as s:
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
        # 本地检查**刻意不吃代理环境**：NO_PROXY 没配时，127.0.0.1 走代理会被误路由、
        # 把在跑的 serve 误报成不可达。local_session 让这个选择在调用点写明白。
        async with local_session() as s:
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


def _check_sandbox() -> Check:
    """Report whether unattended/generated-code execution can be isolated."""
    from src.agents.sandbox import resolve_sandbox
    decision = resolve_sandbox(require_isolation=True)
    if decision.isolated:
        return Check("sandbox", "ok", decision.reason)
    if decision.allowed:  # only explicit off may allow an unattended host run
        return Check("sandbox", "warn", decision.reason)
    return Check("sandbox", "fail", decision.reason)


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


# 长连多久没收到任何帧（含心跳 ping）就该报警。钉钉 Stream 的 ping 是分钟级，
# 十分钟一帧没有基本等于线断了但重连也没成。
_STALE_FRAME_SECONDS = 600


async def _check_im_liveness() -> Check:
    """内嵌桥**活着吗**——不是"凭证配了吗"（那是 _check_im，查的是必要条件）。

    2026-07-28 的事故是"发不出去"；它的镜像盲区是"连接死了收不到"：长连断掉后 poll 循环
    自己退避重连（对的），但此前没有任何面能看出来，表现同样是"机器人装死"。
    读 serve 的 `/api/im/status`（走鉴权，token 从环境取；没设 token 的部署直接放行）。
    """
    import aiohttp
    url = (os.getenv("VORTOCODE_SERVE_URL") or "http://127.0.0.1:8080").rstrip("/")
    token = os.getenv("VORTOCODE_API_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        # 同 _check_serve：本地检查不吃代理环境，否则 127.0.0.1 会被误路由成"不可达"
        async with local_session() as s:
            async with s.get(f"{url}/api/im/status", headers=headers,
                             timeout=aiohttp.ClientTimeout(total=_TIMEOUT)) as r:
                if r.status in (401, 403):
                    return Check("im-live", "warn",
                                 "serve 要鉴权而 VORTOCODE_API_TOKEN 没配到本地环境——"
                                 "查不了桥活性（配上同一个 token 再跑）")
                if r.status != 200:
                    return Check("im-live", "warn", f"取桥活性失败（HTTP {r.status}）")
                data = await r.json()
    except Exception:  # noqa: BLE001 —— serve 没起时 _check_serve 已经报过了，这里不重复报硬伤
        return Check("im-live", "warn", f"serve 不可达，查不了桥活性：{url}")

    if not data.get("bridge"):
        return Check("im-live", "warn", "serve 没有内嵌 IM 桥（`vc server --im dingtalk` 才有）")
    if data.get("error"):
        return Check("im-live", "warn", f"桥状态取用出错：{data['error']}")

    age = data.get("last_frame_age")
    undelivered = int(data.get("undelivered") or 0)
    reconnects = data.get("reconnects", 0)
    tail = f"；{undelivered} 条通知待补发" if undelivered else ""

    if not data.get("connected"):
        return Check("im-live", "fail",
                     f"桥在但**长连是断的**（重连 {reconnects} 次）——手机上的表现是"
                     f"「机器人装死」。最近错误：{data.get('last_error') or '（无）'}{tail}")
    if age is None:
        # **一帧都没收到过 ≠ 连接有问题**，这条是真机实测逼出来的（2026-07-28 20:32 部署后）：
        # 钉钉 Stream 的连接连上 4 分钟仍零帧，而 aiohttp 默认 autoping=True 会把 WebSocket
        # 层的 PING/PONG 在 receive() 里 `continue` 掉、根本不交给上层——也就是说**健康连接
        # 在这个通道上本来就可能观测不到任何帧**。
        #
        # 所以这里只报事实、不判死刑：连着就是连着。把"观测不到"当成"坏了"，600s 后会把
        # 一条好桥报成硬伤——那比误报 warn 恶劣得多（人会去重启一个没坏的服务）。
        # 让活性信号真正可观测要动 transport（autoping=False 自己回 pong），风险另算，
        # 不在健康检查里赌。
        conn_age = data.get("connected_age")
        return Check("im-live", "ok",
                     f"桥连着（已连 {int(conn_age or 0)}s，重连 {reconnects} 次）；"
                     f"本通道未观测到帧——WS 层心跳被 aiohttp 吞掉是正常现象，不据此判死{tail}")
    if age > _STALE_FRAME_SECONDS:
        return Check("im-live", "fail",
                     f"桥 {int(age // 60)} 分钟没收到任何帧（含心跳）——长连多半已死，"
                     f"手机上的表现是「机器人装死」。重连 {data.get('reconnects', 0)} 次；"
                     f"最近错误：{data.get('last_error') or '（无）'}{tail}")
    return Check("im-live", "ok",
                 f"桥活着（{int(age)}s 前收到帧，重连 {data.get('reconnects', 0)} 次）{tail}")


def _check_schedule_timezone(cwd: str) -> Check:
    """定时作业**会在几点跑**——查的是充分条件，不是"服务活着"这个必要条件。

    真机 2026-07-27：VM 时区是 Etc/UTC，`at 09:00` 于是在北京时间 17:00 触发。
    服务 active、作业 enabled、doctor 全绿，而人等了一早上——所有检查都只验了
    "跑不跑得起来"，没有一个验"**几点**跑"。这条把它补上：直接把每个 `at` 作业的
    本地触发时刻算给你看，对不上一眼就知道。下一台新机器必然重踩，所以钉进 doctor。
    """
    import time as _time
    from datetime import datetime
    try:
        from src.gateway.cron import load_jobs
        jobs = [j for j in load_jobs(cwd) if j.enabled]
    except Exception as e:  # noqa: BLE001
        return Check("schedule-tz", "warn", f"读 cron.yaml 失败：{type(e).__name__}: {e}")
    if not jobs:
        return Check("schedule-tz", "ok", "没有启用的定时作业")

    local = datetime.now().astimezone()
    tzname = local.tzname() or _time.tzname[0]
    offset_h = (local.utcoffset().total_seconds() / 3600) if local.utcoffset() else 0.0
    at_jobs = [j for j in jobs if getattr(j.schedule, "kind", "") == "at"]
    detail = f"本机时区 {tzname}（UTC{offset_h:+g}）· {len(jobs)} 个启用作业"

    if at_jobs:
        shown = "、".join(f"{j.name}（{j.schedule.raw}）" for j in at_jobs[:3])
        detail += f"；按本机时区触发：{shown}"
    # UTC 上跑 `at HH:MM` 几乎总是配错——人写 09:00 想的是自己的早上九点
    if at_jobs and abs(offset_h) < 0.01:
        return Check("schedule-tz", "warn",
                     f"{detail}。⚠ 本机是 UTC，`at HH:MM` 会按 UTC 触发——"
                     f"你写的 09:00 在北京时间是 17:00。"
                     f"修：sudo timedatectl set-timezone Asia/Shanghai 后重启服务")
    return Check("schedule-tz", "ok", detail)


async def run_checks(cwd: str) -> List[Check]:
    """跑全部自检，顺序稳定（供人读与测试断言）；单项意外炸 → 记 fail，不拖全体。"""
    import inspect
    plan = [
        ("git", lambda: _check_git(cwd)),
        ("api-key", _check_api_key),
        ("relay", _check_relay),
        ("serve", _check_serve),
        ("sandbox", _check_sandbox),
        ("permissions", lambda: _check_permissions(cwd)),
        ("gh", _check_gh),
        ("im", _check_im),
        ("im-live", _check_im_liveness),
        ("schedule-tz", lambda: _check_schedule_timezone(cwd)),
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
