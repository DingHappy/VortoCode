"""手机签过名的原始请求，经手机网关原样转给常驻助理，由助理**再验一遍**（纵深防御，2026-11-02）。

手机网关暴露在公网上。原来它验完签名就带专用令牌把「参数」转给常驻助理，助理只认令牌——网关一旦被攻破，
拿到令牌的人就能不经手机以主人身份发起对话、下发任务单、批 Codex 动作。现在网关只能转发手机真正签过的
请求：助理用设备登记里的公钥验签、核对设备没被撤销、时间新鲜、nonce 没用过（与网关各记一份，互不影响），
并且**操作由签进去的路径决定**，网关没法把「随手记」改成「下发」；批准类操作还要求是批准钥匙签的。
"""
from __future__ import annotations

import base64
import binascii
import json
import time
from typing import Any, Dict, Optional, Tuple

from src.gateway import mobile_signing as signing

# 手机上签名的路径 → 常驻助理上的操作。只有这些能经转发执行。
PATH_OPS = {
    "/api/mobile/native/capture": "capture",
    "/api/mobile/native/undo": "undo",
    "/api/mobile/native/decide": "decide_codex",
    "/api/mobile/native/dispatch": "dispatch",
    "/api/mobile/native/chat/send": "chat_send",
    "/api/mobile/native/chat/card": "chat_card",
    "/api/mobile/native/goal-advance/resume": "resume_goal",
    "/api/mobile/native/team/message": "team_message",
    "/api/mobile/native/team/decide": "team_decide",
    "/api/mobile/native/team/stop": "team_stop",
}
# 批准时必须是批准钥匙（Face ID）签的操作。对话里的卡片按卡片自己的风险级别在 AppTurns 里判。
_APPROVAL_OPS = {"decide_codex", "dispatch"}
# 不论批准还是拒绝都要批准钥匙：在小组群里说话、退回都会叫醒小组接着干活，验收会结束小组（ADR-017 M3）
_ALWAYS_APPROVAL_OPS = {"team_message", "team_decide"}
_MAX_BODY = 16 * 1024


class EnvelopeRejected(ValueError):
    """转发来的请求没通过核对。原因不细分给调用方（与网关一致：签名问题一律 401）。"""


def build(*, method: str, path: str, body: bytes, headers: Any, device_id: str, key_kind: str) -> Dict[str, str]:
    """网关侧：把这次已核对过的请求原样打包（不改任何字节）。"""
    return {"method": method.upper(), "path": path, "body": base64.b64encode(body).decode("ascii"),
            "timestamp": str(headers.get("x-vc-timestamp") or ""), "nonce": str(headers.get("x-vc-nonce") or ""),
            "signature": str(headers.get("x-vc-signature") or ""), "device_id": device_id, "key_kind": key_kind}


def open_envelope(root: str, envelope: Any, *, now: Optional[float] = None) -> Tuple[str, Dict[str, Any], str, str]:
    """助理侧：核对并拆开。返回 (操作, 请求体, 设备 id, 钥匙类别)；任何一项不对抛 EnvelopeRejected。"""
    from src.gateway.mobile_devices import MobileDevices, MobileStoreError

    if not isinstance(envelope, dict):
        raise EnvelopeRejected("缺少签名请求")
    fields = {key: envelope.get(key) for key in
              ("method", "path", "body", "timestamp", "nonce", "signature", "device_id", "key_kind")}
    if not all(isinstance(value, str) for value in fields.values()):
        raise EnvelopeRejected("签名请求不完整")
    op = PATH_OPS.get(fields["path"])
    if op is None or fields["method"] != "POST" or fields["key_kind"] not in ("device", "approval"):
        raise EnvelopeRejected("这个请求不能经转发执行")
    try:
        body = base64.b64decode(fields["body"], validate=True)
    except (binascii.Error, ValueError) as error:
        raise EnvelopeRejected("请求体无效") from error
    if len(body) > _MAX_BODY:
        raise EnvelopeRejected("请求过大")
    store = MobileDevices(root)
    try:
        if not store.is_active(fields["device_id"]):
            raise EnvelopeRejected("设备已撤销或不存在")
        key = store.signing_key(fields["device_id"], fields["key_kind"])
        if not key:
            raise EnvelopeRejected("这台设备没有可用的钥匙")
        signing.check_freshness(fields["timestamp"], fields["nonce"], time.time() if now is None else now)
        signing.verify(key, signing.message(method=fields["method"], path=fields["path"], body=body,
                                            timestamp=fields["timestamp"], nonce=fields["nonce"],
                                            device_id=fields["device_id"]), fields["signature"])
        # 网关已经记过这个 nonce；助理另记一份（加前缀），同一个签名请求转发第二次就拒
        store.consume_nonce(fields["device_id"], "relay:" + fields["nonce"])
    except signing.SignatureRejected as error:
        raise EnvelopeRejected("签名无效或已过期") from error
    except (ValueError, KeyError, MobileStoreError) as error:
        raise EnvelopeRejected("签名请求没通过核对") from error
    try:
        data = json.loads(body or b"{}")
    except ValueError as error:
        raise EnvelopeRejected("请求体无效") from error
    if not isinstance(data, dict):
        raise EnvelopeRejected("请求体无效")
    if op in _APPROVAL_OPS and data.get("approve") is True and fields["key_kind"] != "approval":
        raise EnvelopeRejected("批准需要批准钥匙")
    if op in _ALWAYS_APPROVAL_OPS and fields["key_kind"] != "approval":
        raise EnvelopeRejected("这个操作需要批准钥匙")
    if op == "chat_card" and data.get("key_kind") != fields["key_kind"]:
        raise EnvelopeRejected("声明的钥匙与签名的钥匙不一致")
    return op, data, fields["device_id"], fields["key_kind"]
