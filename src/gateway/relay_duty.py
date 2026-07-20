"""中转站（ai-token-relay）运营值班巡检——B8-① 试点一（OPC 第一条职责）。

**确定性、零 LLM**：作为 cron `command:` 作业运行（`python -m src.gateway.relay_duty`），
退出码即红绿——0=全部正常（cron 静默，只落 Journal 台账），非 0=有异常（run lane 标 failed
→ 决策队列出一条 `run:<id>` + 通报）。LLM 不在这条路径上；需要"解读"时人从决策队列点开看证据。

检查分三档，**未配置的档跳过且不算异常**（跳过 ≠ 通过，报告里明说）：
- 免鉴权：`GET /api/status`（存活 + 重启检测）、`GET /api/pricing`（DB 读路径活着）。
- 用户 key（`VORTOCODE_RELAY_SK`）：`GET /dashboard/billing/usage`（token 鉴权链路端到端）。
- 管理 token（`VORTOCODE_RELAY_ADMIN_TOKEN`）：`GET /api/channel_pool`（上游渠道冷却/禁用）、
  `GET /api/log/stat`（当日消耗，可配阈值 `VORTOCODE_RELAY_DAILY_QUOTA_LIMIT`）。

重启检测：`/api/status` 的 `start_time` 与上次巡检快照（`.vortocode/duty/relay-state.json`）
比对——变了就报异常（可能是 OOM/崩溃；正常发布重启也会报一次，下一夜自动转绿，宁保守）。
报告只含数值与原因，**绝不回显任何 token**。
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from urllib import error as _urlerror
from urllib import request as _urlrequest

# 生产真身（2026-07-20 实测）：token.vortotech.com /api/status=200；relay 仓库文档里的
# relay.dinghappy.com 已 502（旧域名，后端 frp 链路断）。别照旧文档改回去。
DEFAULT_RELAY_URL = "https://token.vortotech.com"
_TIMEOUT = 10.0
_STATE_REL = Path(".vortocode") / "duty" / "relay-state.json"

# (url, headers) -> (HTTP 状态码, 响应体文本)；连接失败/超时按约定抛异常，由检查项兜住计为异常
Fetch = Callable[[str, dict], Tuple[int, str]]


def _default_fetch(url: str, headers: dict) -> Tuple[int, str]:
    request = _urlrequest.Request(url, headers=headers)
    try:
        with _urlrequest.urlopen(request, timeout=_TIMEOUT) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except _urlerror.HTTPError as error:                      # 非 200 也要拿到 body 当证据
        body = ""
        try:
            body = error.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        return int(error.code), body


@dataclass
class CheckResult:
    name: str
    ok: Optional[bool]          # True=过 / False=异常 / None=未配置跳过
    detail: str

    @property
    def line(self) -> str:
        mark = {True: "✓", False: "✗", None: "-"}[self.ok]
        return f"{mark} {self.name}：{self.detail}"


def _get_json(fetch: Fetch, url: str, headers: dict) -> Tuple[Optional[int], Optional[dict], str]:
    """(状态码, 解析出的 dict, 错误说明)。任何一步失败都转成文字证据，不往外抛。"""
    try:
        status, body = fetch(url, headers)
    except Exception as error:  # noqa: BLE001 —— 连不上/超时本身就是巡检要报告的事实
        return None, None, f"请求失败：{type(error).__name__}: {error}"
    try:
        payload = json.loads(body)
    except ValueError:
        return status, None, f"HTTP {status}，响应不是 JSON（前 120 字：{body[:120]!r}）"
    return status, payload if isinstance(payload, dict) else None, ""


def _load_state(repo_root: str) -> dict:
    try:
        return json.loads((Path(repo_root) / _STATE_REL).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 没有/损坏都当首跑
        return {}


def _save_state(repo_root: str, state: dict) -> None:
    try:
        from src.agents.dev_plan import ensure_state_gitignore

        ensure_state_gitignore(repo_root)
        path = Path(repo_root) / _STATE_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 —— 快照是旁路，写不进去不该把巡检本身标红
        pass


def _check_status(fetch: Fetch, base: str, state: dict) -> Tuple[CheckResult, dict]:
    status, payload, error = _get_json(fetch, f"{base}/api/status", {})
    if error or status != 200 or not payload or payload.get("success") is not True:
        detail = error or f"HTTP {status}，success={payload.get('success') if payload else '无法解析'}"
        return CheckResult("存活 /api/status", False, detail), state
    data = payload.get("data") or {}
    version = str(data.get("version") or "?")
    start_time = data.get("start_time")
    previous = state.get("start_time")
    new_state = dict(state)
    new_state["start_time"] = start_time
    if previous is not None and start_time is not None and start_time != previous:
        def _fmt(value):
            try:
                return datetime.fromtimestamp(int(value)).isoformat(timespec="seconds")
            except Exception:  # noqa: BLE001
                return str(value)
        detail = (f"200 但**进程重启过**（上次巡检 start_time={_fmt(previous)} → 现在 {_fmt(start_time)}，"
                  f"version {version}）——若非主动发布需排查 OOM/崩溃；下一夜自动转绿")
        return CheckResult("存活 /api/status", False, detail), new_state
    return CheckResult("存活 /api/status", True, f"200 success=true · version {version} · 无重启"), new_state


def _check_pricing(fetch: Fetch, base: str) -> CheckResult:
    status, payload, error = _get_json(fetch, f"{base}/api/pricing", {})
    if error or status != 200 or payload is None:
        return CheckResult("价目 /api/pricing", False, error or f"HTTP {status}")
    data = payload.get("data")
    count = len(data) if isinstance(data, (list, dict)) else 0
    if not count:
        return CheckResult("价目 /api/pricing", False, "200 但价目为空（DB 读路径可疑）")
    return CheckResult("价目 /api/pricing", True, f"200，{count} 项")


def _check_billing(fetch: Fetch, base: str, sk: str) -> CheckResult:
    if not sk:
        return CheckResult("计费探针 /dashboard/billing/usage", None, "跳过（未配 VORTOCODE_RELAY_SK）")
    status, payload, error = _get_json(fetch, f"{base}/dashboard/billing/usage",
                                       {"Authorization": f"Bearer {sk}"})
    if error or status != 200 or payload is None:
        return CheckResult("计费探针 /dashboard/billing/usage", False, error or f"HTTP {status}（key 失效/用户被禁？）")
    usage = payload.get("total_usage")
    if not isinstance(usage, (int, float)):
        return CheckResult("计费探针 /dashboard/billing/usage", False, "200 但无 total_usage 数值")
    return CheckResult("计费探针 /dashboard/billing/usage", True, f"200，total_usage={usage}")


def _check_channel_pool(fetch: Fetch, base: str, admin: str) -> CheckResult:
    if not admin:
        return CheckResult("渠道池 /api/channel_pool", None, "跳过（未配 VORTOCODE_RELAY_ADMIN_TOKEN）")
    status, payload, error = _get_json(fetch, f"{base}/api/channel_pool",
                                       {"Authorization": admin})
    if error or status != 200 or payload is None:
        return CheckResult("渠道池 /api/channel_pool", False, error or f"HTTP {status}（管理 token 失效？）")
    data = payload.get("data") or {}
    cooling = data.get("cooling_count") or 0
    channels = data.get("channels") or []
    bad = [c for c in channels if isinstance(c, dict) and c.get("status") not in (1, None)]
    if cooling or bad:
        parts = []
        for channel in (bad or channels)[:5]:
            if not isinstance(channel, dict):
                continue
            label = channel.get("name") or channel.get("id")
            reason = channel.get("cool_reason") or f"status={channel.get('status')}"
            parts.append(f"{label}: {reason}")
        return CheckResult("渠道池 /api/channel_pool", False,
                           f"{cooling} 个渠道冷却中 / {len(bad)} 个非启用：{'；'.join(parts)}")
    return CheckResult("渠道池 /api/channel_pool", True, f"{len(channels)} 个渠道全部在线，无冷却")


def _check_daily_quota(fetch: Fetch, base: str, admin: str, limit: Optional[float],
                       now: float) -> CheckResult:
    if not admin:
        return CheckResult("当日消耗 /api/log/stat", None, "跳过（未配 VORTOCODE_RELAY_ADMIN_TOKEN）")
    local_midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    url = (f"{base}/api/log/stat?type=2"
           f"&start_timestamp={int(local_midnight.timestamp())}&end_timestamp={int(now)}")
    status, payload, error = _get_json(fetch, url, {"Authorization": admin})
    if error or status != 200 or payload is None:
        return CheckResult("当日消耗 /api/log/stat", False, error or f"HTTP {status}")
    quota = (payload.get("data") or {}).get("quota")
    if not isinstance(quota, (int, float)):
        return CheckResult("当日消耗 /api/log/stat", False, "200 但无 quota 数值")
    if limit is not None and quota > limit:
        return CheckResult("当日消耗 /api/log/stat", False,
                           f"quota={quota} **超过日阈值 {limit}**（某个 token 跑飞/被薅？"
                           f"用 /api/reconciliation 按分组归因）")
    suffix = f"（阈值 {limit}）" if limit is not None else "（未配阈值，仅记录）"
    return CheckResult("当日消耗 /api/log/stat", True, f"quota={quota} {suffix}")


def run_duty(repo_root: str, *, fetch: Fetch = _default_fetch,
             env: Optional[dict] = None, now: Optional[float] = None) -> Tuple[int, str]:
    """跑一轮巡检，返回 (退出码, 报告文本)。0=全部正常；1=有异常。跳过项不影响退出码。"""
    environ = os.environ if env is None else env
    base = str(environ.get("VORTOCODE_RELAY_URL") or DEFAULT_RELAY_URL).strip().rstrip("/")
    sk = str(environ.get("VORTOCODE_RELAY_SK") or "").strip()
    admin = str(environ.get("VORTOCODE_RELAY_ADMIN_TOKEN") or "").strip()
    raw_limit = str(environ.get("VORTOCODE_RELAY_DAILY_QUOTA_LIMIT") or "").strip()
    try:
        limit: Optional[float] = float(raw_limit) if raw_limit else None
    except ValueError:
        limit = None
    moment = time.time() if now is None else now

    state = _load_state(repo_root)
    results: List[CheckResult] = []
    status_result, new_state = _check_status(fetch, base, state)
    results.append(status_result)
    results.append(_check_pricing(fetch, base))
    results.append(_check_billing(fetch, base, sk))
    results.append(_check_channel_pool(fetch, base, admin))
    results.append(_check_daily_quota(fetch, base, admin, limit, moment))
    _save_state(repo_root, new_state)

    failed = [r for r in results if r.ok is False]
    skipped = [r for r in results if r.ok is None]
    stamp = datetime.fromtimestamp(moment).isoformat(timespec="minutes")
    lines = [f"中转站值班巡检 · {base} · {stamp}"]
    lines += [r.line for r in results]
    if failed:
        lines.append(f"结论：{len(failed)} 项异常（见 ✗ 行证据）")
    elif skipped:
        lines.append(f"结论：已查项全部正常，{len(skipped)} 项未配置跳过（跳过 ≠ 通过）")
    else:
        lines.append("结论：全部正常")
    return (1 if failed else 0), "\n".join(lines)


def main() -> int:
    code, report = run_duty(os.getcwd())
    print(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
