"""手机写请求的设备签名（ADR-011 / ADR-009 修订）。

每台已授权设备可以绑定两把 P-256 公钥（iOS Secure Enclave 只支持这条曲线，私钥不出设备）：

- ``device``：日常写入（随手记、撤销）。
- ``approval``：批准动作。App 生成这把私钥时要求 Face ID / Touch ID 解锁，**所以「批准必须过生物识别」
  由密码学保证**，不是靠 App 自觉——服务端只认这把钥匙签的批准。

签名的内容把这一次请求钉死：方法、路径、请求体 SHA-256、时间戳、一次性 nonce、设备 id。时间戳偏差超过
``MAX_SKEW`` 或 nonce 用过都拒绝（防重放）。批准请求体里还带动作摘要 ``action_digest``：服务端按当前动作
重新计算，对不上就拒绝——批的是 A，执行的就必须是 A。

公钥格式：X9.63 未压缩点（65 字节，0x04‖X‖Y）的 base64，即 iOS ``SecKeyCopyExternalRepresentation`` 的输出；
签名是 DER 编码的 ECDSA-SHA256（``.ecdsaSignatureMessageX962SHA256``）的 base64。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any

MAX_SKEW = 300                       # 秒
KEY_KINDS = ("device", "approval")
_NONCE = re.compile(r"[A-Za-z0-9_-]{16,64}")


class SignatureRejected(ValueError):
    """签名缺失、格式不对、过期、重放或验不过。对外统一 401，不说明哪一项。"""


def _b64(value: Any, *, limit: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise SignatureRejected("编码无效")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SignatureRejected("编码无效") from error


def normalize_public_key(value: Any) -> str:
    """校验并规范化一把公钥；不在 P-256 曲线上的点直接拒绝。"""
    from cryptography.hazmat.primitives.asymmetric import ec

    raw = _b64(value, limit=120)
    if len(raw) != 65 or raw[0] != 4:
        raise SignatureRejected("公钥需要 65 字节的未压缩 P-256 点")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as error:
        raise SignatureRejected("公钥不在 P-256 曲线上") from error
    return base64.b64encode(raw).decode("ascii")


def message(*, method: str, path: str, body: bytes, timestamp: str, nonce: str, device_id: str) -> bytes:
    """被签名的内容。换行分隔，各字段都已规范化，不存在拼接歧义。"""
    return "\n".join(("vortocode-mobile-v1", method.upper(), path, hashlib.sha256(body).hexdigest(),
                      timestamp, nonce, device_id)).encode("utf-8")


def check_freshness(timestamp: Any, nonce: Any, now: float) -> tuple[int, str]:
    if not isinstance(timestamp, str) or not timestamp.isdigit() or len(timestamp) > 12:
        raise SignatureRejected("时间戳无效")
    stamp = int(timestamp)
    if abs(now - stamp) > MAX_SKEW:
        raise SignatureRejected("请求已过期")
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise SignatureRejected("nonce 无效")
    return stamp, nonce


def verify(public_key: str, signed: bytes, signature: Any) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    raw = _b64(public_key, limit=120)
    der = _b64(signature, limit=200)
    try:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        key.verify(der, signed, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as error:
        raise SignatureRejected("签名验证失败") from error


def action_digest(action: dict) -> str:
    """待批动作的摘要：对象、正文和它绑定的轮次。任何一项变了，旧批准都不作数。"""
    fields = {key: str(action.get(key) or "") for key in ("id", "kind", "thread_id", "thread_cwd", "turn_id", "text")}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
