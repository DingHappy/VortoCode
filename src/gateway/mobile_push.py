"""给主人的手机 App 推送（APNs），正文端到端加密（ADR-011 防线 4 / 审查 R10）。

正文不明文交给苹果：每条推送只带固定的占位文字和一段密文 ``enc``。App 的通知扩展（Notification Service
Extension）用 Secure Enclave 里的推送钥匙解密后替换显示——苹果和中间链路看不到内容，锁屏上照样能看到。

加密格式与 iOS ``SecKeyCreateDecryptedData(key, .eciesEncryptionStandardX963SHA256AESGCM, data)`` 一致：
临时 P-256 公钥（65 字节未压缩点）‖ AES-GCM 密文 ‖ 16 字节标签；共享密钥经 ANSI X9.63 KDF（SHA-256，
sharedInfo = 临时公钥）派生 16 字节密钥；Standard 变体的 IV 是 16 个零字节（每条推送的临时钥匙都不同，
所以同一 (key, IV) 不会重复）。

APNs 配置只从环境变量读（不在仓库、不进日志）：``VORTOCODE_APNS_KEY_FILE``（.p8 私钥路径）、
``VORTOCODE_APNS_KEY_ID``、``VORTOCODE_APNS_TEAM_ID``、``VORTOCODE_APNS_TOPIC``（App 的 bundle id）。
没配齐就不推，返回 False，不影响 IM 那一路。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

PLACEHOLDER = "有一条新消息，打开 VortoCode 查看"
_TOKEN = re.compile(r"[0-9a-f]{64,200}")
_HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
_JWT_TTL = 30 * 60                     # APNs 要求 1 小时内换新、不早于 20 分钟；取中间
_jwt_cache: dict[str, Any] = {}


def normalize_registration(apns_token: Any, push_key: Any, environment: Any) -> dict:
    from src.gateway.mobile_signing import normalize_public_key

    if not isinstance(apns_token, str) or not _TOKEN.fullmatch(apns_token.lower()):
        raise ValueError("APNs 设备令牌无效")
    if environment not in _HOSTS:
        raise ValueError("推送环境只能是 production 或 sandbox")
    return {"token": apns_token.lower(), "key": normalize_public_key(push_key), "environment": environment}


def encrypt(push_key_b64: str, text: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.x963kdf import X963KDF

    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(push_key_b64))
    ephemeral = ec.generate_private_key(ec.SECP256R1())
    ephemeral_raw = ephemeral.public_key().public_bytes(serialization.Encoding.X962,
                                                        serialization.PublicFormat.UncompressedPoint)
    shared = ephemeral.exchange(ec.ECDH(), receiver)
    key = X963KDF(algorithm=hashes.SHA256(), length=16, sharedinfo=ephemeral_raw).derive(shared)
    sealed = AESGCM(key).encrypt(b"\x00" * 16, text.encode("utf-8"), None)
    return base64.b64encode(ephemeral_raw + sealed).decode("ascii")


def configured() -> bool:
    return all(os.getenv(name, "").strip() for name in
               ("VORTOCODE_APNS_KEY_FILE", "VORTOCODE_APNS_KEY_ID", "VORTOCODE_APNS_TEAM_ID", "VORTOCODE_APNS_TOPIC"))


def _jwt(now: float) -> str:
    """APNs 的提供方令牌（ES256）。私钥每次从文件读、不缓存；令牌本身缓存 30 分钟。"""
    cached = _jwt_cache.get("token")
    if cached and now - _jwt_cache.get("issued", 0) < _JWT_TTL:
        return str(cached)
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    with open(os.environ["VORTOCODE_APNS_KEY_FILE"], "rb") as handle:
        private = serialization.load_pem_private_key(handle.read(), password=None)
    if not isinstance(private, ec.EllipticCurvePrivateKey):
        raise ValueError("APNs 私钥不是 EC 钥匙")

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode()

    head = part({"alg": "ES256", "kid": os.environ["VORTOCODE_APNS_KEY_ID"].strip()})
    body = part({"iss": os.environ["VORTOCODE_APNS_TEAM_ID"].strip(), "iat": int(now)})
    r, s = decode_dss_signature(private.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    signature = base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).rstrip(b"=").decode()
    token = f"{head}.{body}.{signature}"
    _jwt_cache.update(token=token, issued=now)
    return token


def payload(registration: dict, text: str) -> dict:
    return {"aps": {"alert": {"title": "VortoCode", "body": PLACEHOLDER}, "mutable-content": 1, "sound": "default"},
            "enc": encrypt(registration["key"], text[:2000])}


async def send(registration: dict, text: str, *, client: Any = None) -> str:
    """推一条；返回 ``ok`` / ``gone``（令牌已失效，调用方该清掉）/ ``failed``。不重试。"""
    import httpx

    url = f"{_HOSTS[registration['environment']]}/3/device/{registration['token']}"
    headers = {"authorization": f"bearer {_jwt(time.time())}", "apns-topic": os.environ["VORTOCODE_APNS_TOPIC"].strip(),
               "apns-push-type": "alert", "apns-priority": "10"}
    own = client is None
    client = client or httpx.AsyncClient(http2=True, timeout=10)
    try:
        response = await client.post(url, headers=headers, json=payload(registration, text))
    except httpx.HTTPError as error:
        logger.warning("APNs 推送失败：%s", type(error).__name__)
        return "failed"
    finally:
        if own:
            await client.aclose()
    if response.status_code == 200:
        return "ok"
    reason = ""
    try:
        reason = str(response.json().get("reason") or "")
    except ValueError:
        pass
    if response.status_code == 410 or reason in {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}:
        return "gone"
    if response.status_code == 403:
        _jwt_cache.clear()                # 令牌过期或被拒：下次重新签
    logger.warning("APNs 拒绝推送：%s %s", response.status_code, reason[:60])
    return "failed"


async def push_owner(root: str, text: str) -> bool:
    """推给这台助理所有已登记推送的 App 设备。有一台送达（APNs 受理）就算 True；不代表用户已读。"""
    if not configured():
        return False
    from src.gateway.mobile_devices import MobileDevices

    store = MobileDevices(root)
    try:
        targets = store.push_targets()
    except Exception as error:  # noqa: BLE001 —— 推送是附加通道，读不到登记不影响 IM
        logger.warning("读取推送登记失败：%s", type(error).__name__)
        return False
    delivered = False
    for device_id, registration in targets:
        try:
            result = await send(registration, text)
        except Exception as error:  # noqa: BLE001 —— 单台设备失败不拖累其它设备和 IM
            logger.warning("APNs 推送异常：%s", type(error).__name__)
            continue
        if result == "gone":
            store.forget_push(device_id, registration["token"])
        delivered = delivered or result == "ok"
    return delivered
