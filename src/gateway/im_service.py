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




def build_adapter(channel: str, *, env=None) -> Tuple[object, str]:
    """按 env 凭证构造通道 adapter，返回 (adapter, owner_id)。缺凭证抛 IMConfigError（fail-closed）。"""
    channel = (channel or "").strip().lower()
    values = os.environ if env is None else env
    if channel == "telegram":
        token = values.get("VORTOCODE_TG_TOKEN", "").strip()
        owner = values.get("VORTOCODE_TG_OWNER_ID", "").strip()
        if token and not owner:
            from src.im.telegram_pairing import PairingError, load_paired_owner
            try:
                owner = load_paired_owner(token)
            except PairingError as exc:
                raise IMConfigError(str(exc)) from exc
        if not token or not owner:
            raise IMConfigError(
                "Telegram 桥需要环境变量 VORTOCODE_TG_TOKEN 和 VORTOCODE_TG_OWNER_ID"
                "（或先运行 vc im-pair telegram 私聊配对；缺失时 fail-closed）。")
        from src.im.telegram import TelegramAdapter
        # bot_username 是**可选**的（群提及门用；不设则首次群消息时 getMe 惰性解析），
        # 刻意不进上面的 fail-closed 必填项——它缺失只会让群消息更不响应，不影响私聊凭证路径。
        return TelegramAdapter(
            token, owner,
            bot_username=values.get("VORTOCODE_TG_BOT_USERNAME", "").strip() or None), owner
    if channel == "dingtalk":
        cid = values.get("VORTOCODE_DD_CLIENT_ID", "").strip()
        secret = values.get("VORTOCODE_DD_CLIENT_SECRET", "").strip()
        owner = values.get("VORTOCODE_DD_OWNER_ID", "").strip()
        if not cid or not secret or not owner:
            raise IMConfigError(
                "钉钉桥需要环境变量 VORTOCODE_DD_CLIENT_ID / VORTOCODE_DD_CLIENT_SECRET / "
                "VORTOCODE_DD_OWNER_ID（配对制，fail-closed）。\n"
                "  钉钉开放平台建企业内机器人应用（Stream 模式）拿 AppKey(ClientID)/AppSecret；"
                "OWNER_ID 填你自己的 senderStaffId（给机器人发条消息即可在回调里看到）。")
        from src.im.dingtalk import DingTalkAdapter
        return DingTalkAdapter(cid, secret, owner), owner
    raise IMConfigError(f"未知 IM 通道 {channel!r}（可选 telegram / dingtalk）")


def load_allow_from(channel: str):
    """读入站白名单配置，返回 None（未配置 → 由 bridge 回落到"只放 owner"）或一个 id 集合。

    通道专属 `VORTOCODE_TG_ALLOW_FROM` / `VORTOCODE_DD_ALLOW_FROM` 优先于通用
    `VORTOCODE_IM_ALLOW_FROM`；逗号/空白分隔。

    **"未设置"与"设成空"是两回事**（fail-closed 的关键）：环境变量不存在 = 沿用配对制只放 owner；
    存在但解析为空（`VORTOCODE_IM_ALLOW_FROM=` 或 `","`）= **显式空白名单 = 全拒**，连 owner
    也进不来。绝不把"空"读成"不限制"。凭证读取（build_adapter）完全不受本函数影响。
    """
    from src.im.bridge import parse_allow_from
    channel_key = {"telegram": "VORTOCODE_TG_ALLOW_FROM",
                   "dingtalk": "VORTOCODE_DD_ALLOW_FROM"}.get((channel or "").strip().lower())
    for key in (channel_key, "VORTOCODE_IM_ALLOW_FROM"):
        if key and key in os.environ:
            return parse_allow_from(os.environ[key])
    return None


def load_conversation() -> str:
    """One config path for embedded and standalone IM; a typo cannot join primary chat."""
    conversation = os.getenv("VORTOCODE_IM_CONVERSATION", "primary").strip().lower()
    if conversation not in {"primary", "isolated"}:
        raise IMConfigError("VORTOCODE_IM_CONVERSATION must be primary or isolated")
    return conversation


def load_session_role() -> str:
    """Trusted host opt-in, shared by standalone and embedded IM."""
    role = os.getenv("VORTOCODE_IM_POLICY", "standard").strip().lower()
    if role not in {"standard", "research_read"}:
        raise IMConfigError("VORTOCODE_IM_POLICY must be standard or research_read")
    if role == "research_read" and (load_conversation() != "isolated" or
                                   os.getenv("VORTOCODE_IM_ROLE", "interactive").strip().lower() != "interactive"):
        raise IMConfigError("research_read requires isolated interactive IM")
    if role == "research_read" and os.getenv("VORTOCODE_WORKSPACE_SCOPE", "").strip().lower() != "project":
        raise IMConfigError("research_read requires explicit VORTOCODE_WORKSPACE_SCOPE=project")
    return role


def start_embedded(channel: str, repo_root: str, *, mode: str = "plan",
                   adapter=None, owner: Optional[str] = None, runner=None, register_worker=None,
                   binding_id=None, bot_id=None, conversation=None, session_role=None,
                   notification_only=None, owner_ref="paired-owner", claim_turn=None, primary_binding=None):
    """Register a legacy bridge or an explicitly addressed host binding."""
    from src.gateway import im_registry
    legacy = binding_id is None
    if legacy:
        role = os.getenv("VORTOCODE_IM_ROLE", "interactive").strip().lower()
        if role not in {"interactive", "notify"}:
            raise IMConfigError("VORTOCODE_IM_ROLE must be interactive or notify")
        conversation = load_conversation()
        session_role = load_session_role()
        notification_only = role == "notify"
    elif conversation not in {"primary", "isolated"} or session_role not in {"standard", "research_read"}:
        raise IMConfigError("Hosted bindings require explicit conversation and policy")
    if notification_only and conversation == "isolated":
        raise IMConfigError("An isolated bot cannot own the primary notification route")
    if runner is None or register_worker is None:
        raise IMConfigError("Embedded IM requires an injected Runtime runner and worker registrar")
    if adapter is None:
        adapter, owner = build_adapter(channel)
    if notification_only and channel == "telegram":
        adapter.notification_only = True
    return im_registry.register(channel, repo_root, adapter=adapter, owner=owner, runner=runner,
                                register_worker=register_worker, mode=mode, conversation=conversation,
                                session_role=session_role, notification_only=bool(notification_only),
                                allow_from=load_allow_from(channel),
                                binding_id=binding_id, bot_id=bot_id, owner_ref=owner_ref, claim_turn=claim_turn,
                                primary_binding=primary_binding)


def stop_embedded(binding_id=None, *, expected=None) -> None:
    """Unregister stopped bindings; managed callers await their host stop first."""
    from src.gateway import im_registry
    if binding_id is None:
        for binding in im_registry.entries():
            im_registry.unregister(binding.id, expected=expected)
    else:
        im_registry.unregister(binding_id, expected=expected)


def managed_session_bridge(root, key):
    from src.gateway.im_registry import entries
    from pathlib import Path
    return next((item.bridge for item in entries()
                 if item.root == str(Path(root).resolve()) and item.bridge._sid == key
                 and getattr(item.bridge, "_input_controller", None) is not None), None)


def is_managed_session(root, key):
    from src.gateway.session_store import load_session
    if managed_session_bridge(root, key) is not None:
        return True
    return (load_session(root, key) or {}).get("managed_im") is True


def current_bridge(binding_id=None):
    from src.gateway.im_registry import current
    return current(binding_id)


def bridge_liveness() -> dict:
    """Keep the primary status contract and expose addressed, non-secret bindings."""
    from src.gateway import im_registry
    bridge = current_bridge()
    try:
        primary = {"bridge": True, **bridge.liveness()} if bridge is not None else {"bridge": False}
    except Exception:
        primary = {"bridge": True, "error": "liveness_unavailable"}
    bindings = im_registry.status()
    if bindings and bindings[0]["binding_id"] != "legacy":
        primary["bindings"] = bindings
    return primary


async def notify_owner(text: str) -> bool:
    """把一条后台通知推给已配对 owner（serve 内嵌了 bridge 才有；没有/失败返回 False，不抛）。

    scheduler 通知三路里的 IM 路（#129 收口）：台账/WS 之外，人不在电脑前也能收到。
    """
    from src.gateway import im_runtime
    return await im_runtime.notify_owner(text)
