"""IM 桥内嵌服务（b3 PR-5）——把 IM bridge 并进 serve 进程，单进程唯一状态所有者。

此前 `vc im` 是独立进程：它与 serve 各持一个 TaskRunner/会话，同一 .vortocode 有两个
状态所有者。现在 serve 可 **opt-in** 内嵌 bridge（`vc server --im telegram|dingtalk` 或
env `VORTOCODE_IM`）：

- 复用 bridge 的全部既有面（ChannelAdapter/配对制/纯出站/按钮确认），零改协议；
- **共享 serve 的 TaskRunner**（单一并发池/台账/订阅集）：IM 的 /task 提交 kind="im-dev"，
  由 tasks._dev_worker 按 kind 分发回 bridge 的 worker——IM 在跑中的按钮确认 UX 原样保留，
  WS 客户端同时能看到 IM 提交的任务（同一订阅集）；
- **通知第三路**（#129 遗留收口）：scheduler 的 cron/heartbeat 通知除台账 + WS 广播外，
  推给已配对 owner（notify_owner，best-effort——IM 断线不拖垮调度）。

`vc im` 独立模式保留（没有 serve 也能用）；凭证 fail-closed 照旧（缺配置拒启）。
"""

from __future__ import annotations

import os
from typing import Optional, Tuple


class IMConfigError(Exception):
    """IM 通道凭证缺失/不合法（fail-closed：宁可拒启，不带残缺配对上线）。"""


_ACTIVE: dict = {"bridge": None, "unsubscribe": None}   # serve 内嵌单例（standalone 模式不登记）


def build_adapter(channel: str) -> Tuple[object, str]:
    """按 env 凭证构造通道 adapter，返回 (adapter, owner_id)。缺凭证抛 IMConfigError（fail-closed）。"""
    channel = (channel or "").strip().lower()
    if channel == "telegram":
        token = os.getenv("VORTOCODE_TG_TOKEN", "").strip()
        owner = os.getenv("VORTOCODE_TG_OWNER_ID", "").strip()
        if not token or not owner:
            raise IMConfigError(
                "Telegram 桥需要环境变量 VORTOCODE_TG_TOKEN 和 VORTOCODE_TG_OWNER_ID"
                "（配对制，fail-closed）。\n"
                "  ① 找 @BotFather 建 bot 拿 token；② 给 bot 发一条消息，再从 "
                "https://api.telegram.org/bot<token>/getUpdates 读你自己的数字 chat id。")
        from src.im.telegram import TelegramAdapter
        return TelegramAdapter(token, owner), owner
    if channel == "dingtalk":
        cid = os.getenv("VORTOCODE_DD_CLIENT_ID", "").strip()
        secret = os.getenv("VORTOCODE_DD_CLIENT_SECRET", "").strip()
        owner = os.getenv("VORTOCODE_DD_OWNER_ID", "").strip()
        if not cid or not secret or not owner:
            raise IMConfigError(
                "钉钉桥需要环境变量 VORTOCODE_DD_CLIENT_ID / VORTOCODE_DD_CLIENT_SECRET / "
                "VORTOCODE_DD_OWNER_ID（配对制，fail-closed）。\n"
                "  钉钉开放平台建企业内机器人应用（Stream 模式）拿 AppKey(ClientID)/AppSecret；"
                "OWNER_ID 填你自己的 senderStaffId（给机器人发条消息即可在回调里看到）。")
        from src.im.dingtalk import DingTalkAdapter
        return DingTalkAdapter(cid, secret, owner), owner
    raise IMConfigError(f"未知 IM 通道 {channel!r}（可选 telegram / dingtalk）")


def start_embedded(channel: str, repo_root: str, *, mode: str = "plan",
                   adapter=None, owner: Optional[str] = None, runner=None):
    """在 serve 进程内起 bridge：构造（或注入，测试用）adapter → 建 bridge（共享 runner）→
    登记单例 + kind 分发 → 返回 (bridge, adapter)。调用方负责 asyncio.create_task(bridge.run())。
    """
    from src.im.bridge import IMBridge
    if adapter is None:
        adapter, owner = build_adapter(channel)
    if runner is None:
        from src.web.routers.tasks import get_runner
        runner = get_runner()
    bridge = IMBridge(repo_root, adapter, str(owner), channel=channel, mode=mode, runner=runner)
    from src.web.routers import tasks as tasks_router
    tasks_router.register_im_worker(bridge._task_worker)   # kind="im-dev" 分发回 bridge worker
    # IM 也收任务进度/终态（与 WS 同一订阅集）；unsubscribe 必须留着——stop 时不退订的话，
    # lifespan 重启/动态启停会把更新继续投给已停的 bridge/adapter（评审抓的订阅泄漏）
    _ACTIVE["unsubscribe"] = runner.subscribe(bridge._on_task_update)
    _ACTIVE["bridge"] = bridge
    return bridge, adapter


def stop_embedded() -> None:
    """注销内嵌 bridge（serve 关停时调；adapter 的关闭由调用方负责）：单例、kind 分发、订阅全清。"""
    _ACTIVE["bridge"] = None
    unsub = _ACTIVE.pop("unsubscribe", None)
    _ACTIVE["unsubscribe"] = None
    if unsub is not None:
        try:
            unsub()                                        # 从共享 runner 退订（防泄漏到已停 bridge）
        except Exception:  # noqa: BLE001
            pass
    try:
        from src.web.routers import tasks as tasks_router
        tasks_router.register_im_worker(None)
    except Exception:  # noqa: BLE001
        pass


def current_bridge():
    return _ACTIVE["bridge"]


async def notify_owner(text: str) -> bool:
    """把一条后台通知推给已配对 owner（serve 内嵌了 bridge 才有；没有/失败返回 False，不抛）。

    scheduler 通知三路里的 IM 路（#129 收口）：台账/WS 之外，人不在电脑前也能收到。
    """
    bridge = _ACTIVE["bridge"]
    if bridge is None:
        return False
    try:
        await bridge._safe_send(str(text))
        return True
    except Exception:  # noqa: BLE001 —— IM 断线不拖垮调度循环
        return False
