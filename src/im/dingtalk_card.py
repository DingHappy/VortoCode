"""钉钉互动卡片——把确认从"回复 y/n"变成**真按钮**。

## 为什么值得做

确认是这套系统里人最常做的动作（每次写文件/跑命令/开 PR/改作业都要点头）。现在的形态是
一行"请回复 **y**（批准）或 **n**（拒绝）"——功能上没问题，但每次都要切输入法打字，
而且历史消息里全是孤零零的 y/n，回头翻记录根本看不出批的是什么。按钮把这两件事一起解决。

## 三条纪律（都是为了"做坏了也不会把 bot 弄死"）

1. **没配模板 = 完全不启用**，且此时 Stream 建连的 payload 与不加本功能时**逐字节相同**。
   加订阅主题会改建连请求，万一钉钉拒绝未知主题，长连建不起来就是 bot 直接死——
   这个风险本地验不了，所以把它锁在"配了才有"的门后面。
2. **卡片任何一步出错都退回文本 y/n**。卡片是体验优化，确认能力本身一秒都不能丢
   （2026-07-28 刚被"投递静默失败"教育过：交付路径宁可难看，不可断）。
3. **按钮点击仍走 bridge 的三道入站闸**。卡片回调只是换了一种"消息"的形状，
   白名单/群提及/审批只认主人这三条一条都不能少——换个入口就绕过闸是最典型的漏法。

## 主人要做什么（一次性）

在钉钉开放平台「卡片平台」建一个互动卡片模板，放两个按钮，回调类型选 **Stream**，
然后把模板 ID 配进 env：

    VORTOCODE_DD_CARD_TEMPLATE_ID=<模板ID>

模板变量约定（卡片正文里引用这几个）：
    ``title`` 标题 · ``body`` 正文（要批准的操作）· ``status`` 结果（点完后回填）
两个按钮的 **key/value** 分别填 ``approve`` 与 ``deny``。

没配 → 一切照旧走文本 y/n，不影响任何现有行为。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

_log = logging.getLogger("vortocode.im.dingtalk.card")

# Stream 模式下卡片按钮回调的主题。**只在配了模板时才订阅**（见模块 docstring 第 1 条）。
CARD_CALLBACK_TOPIC = "/v1.0/card/instances/callback"

_CREATE_AND_DELIVER = "https://api.dingtalk.com/v1.0/card/instances/createAndDeliver"
_UPDATE = "https://api.dingtalk.com/v1.0/card/instances"

APPROVE_KEY = "approve"
DENY_KEY = "deny"


def card_template_id() -> str:
    """配了模板 ID 才启用卡片确认；没配返回空串（= 走文本 y/n）。"""
    return os.getenv("VORTOCODE_DD_CARD_TEMPLATE_ID", "").strip()


def robot_space_id(owner_id: str) -> str:
    """单聊机器人会话的 openSpaceId。收件人恒为已配对 owner，与 send_text 的主动通道同口径。"""
    return f"dtv1.card//IM_ROBOT.{owner_id}"


def build_card_payload(template_id: str, owner_id: str, callback_id: str, text: str) -> dict:
    """拼 createAndDeliver 的请求体。抽成纯函数是为了能离线断言形状（不触网也测得了）。"""
    title, body = _split_title(text)
    return {
        "cardTemplateId": template_id,
        "outTrackId": callback_id,          # 回调靠它认出是哪次确认（就用 bridge 的 cid）
        "callbackType": "STREAM",
        "openSpaceId": robot_space_id(owner_id),
        "cardData": {"cardParamMap": {"title": title, "body": body, "status": ""}},
        "imRobotOpenSpaceModel": {"supportForward": False},
        "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
        "userIdType": 1,
    }


def build_update_payload(callback_id: str, approved: bool) -> dict:
    """点完之后把卡片刷成终态。

    为什么必须刷：按钮留在那儿还能点，人第二天翻记录会以为这条还等着自己——而实际上
    确认早就被消费掉了（Future 已 resolve）。卡片必须自己说清"这条已经批过/拒过"。
    """
    mark = "✅ 已批准" if approved else "❌ 已拒绝"
    return {
        "outTrackId": callback_id,
        "cardData": {"cardParamMap": {"status": mark}},
        "userIdType": 1,
    }


def _split_title(text: str) -> tuple:
    """把确认文案拆成标题 + 正文：首行当标题，其余当正文。

    污点警示（"⚠️ 本回合摄入过外部内容…"）就在首行——它必须留在**标题**里，
    那是卡片上最显眼的位置。把安全提示挤到正文末尾等于没提示。
    """
    lines = str(text or "").splitlines()
    idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if idx is None:                            # 空文案也不许炸：炸了卡片就静默降级回文本
        return "需要你确认", "需要你确认"
    head = lines[idx].strip()
    rest = "\n".join(lines[idx + 1:]).strip()
    return head[:120], (rest or head)[:2000]


def parse_card_callback(data: dict) -> Optional[tuple]:
    """把一帧卡片回调解析成 ``(callback_id, approved, sender_id)``；不是按钮点击 → None。

    钉钉在不同版本里把按钮值放在几个不同的地方（cardActionData / actionData / content / params），
    这里逐个试——**认不出就返回 None**（宁可当没看见，也不要把一次未知交互当成"批准"）。
    fail-closed 的方向永远是"不放行"。

    真实形状已对照官方 Go SDK 的 ``card.CardRequest`` 核过（open-dingtalk/dingtalk-stream-sdk-go）：
    顶层 ``outTrackId`` / ``userId``，按钮参数在 ``cardActionData.cardPrivateData.params``，
    参数名由模板自定（官方示例用 ``action``）。本函数的多形状扫描覆盖这条真实路径。
    """
    if not isinstance(data, dict):
        return None
    track = str(data.get("outTrackId") or data.get("cardInstanceId") or "").strip()
    sender = str(data.get("userId") or data.get("senderStaffId") or "").strip()
    if not track or not sender:
        # 没有发送者身份的点击同样算"认不出"：宁可按钮失效（文本 y/n 兜底还在），
        # 也绝不让下游把一次无名交互记到任何人头上——那会把 bridge 的闸架空。
        return None

    blob: dict = {}
    for key in ("cardActionData", "actionData", "content", "params"):
        raw = data.get(key)
        if isinstance(raw, dict):
            blob = raw
            break
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                blob = parsed
                break
    if not blob:
        return None

    # 值可能在 cardPrivateData.params / params / 直接平铺
    for holder in (blob.get("cardPrivateData"), blob):
        if isinstance(holder, dict):
            params = holder.get("params") if isinstance(holder.get("params"), dict) else holder
            for field in ("action", "value", "key", "buttonKey"):
                val = str(params.get(field) or "").strip().lower()
                if val == APPROVE_KEY:
                    return track, True, sender
                if val == DENY_KEY:
                    return track, False, sender
    return None


class CardSender:
    """卡片的出站面。transport 可注入（同 connect_fn/reply_fn/oto_fn 的约定，测试不触网）。

    真实实现要 access_token，与发媒体共用适配器上那套缓存——所以这里不自己取 token，
    由适配器把 ``token_fn`` / ``session_fn`` 传进来。
    """

    def __init__(self, template_id: str, owner_id: str, *, token_fn=None, session_fn=None,
                 post_fn=None):
        self.template_id = template_id
        self.owner_id = str(owner_id)
        self._token_fn = token_fn
        self._session_fn = session_fn
        self._post_fn = post_fn            # async (url, payload, token) -> dict；None = 真 HTTP

    async def _post(self, url: str, payload: dict) -> dict:
        if self._post_fn is not None:
            return await self._post_fn(url, payload, "")
        token = await self._token_fn(legacy=False)
        session = await self._session_fn()
        async with session.post(url, json=payload,
                                headers={"x-acs-dingtalk-access-token": token}) as r:
            body = await r.text()
            if r.status != 200:
                raise RuntimeError(f"卡片接口 HTTP {r.status}: {body[:200]}")
            try:
                return json.loads(body or "{}")
            except ValueError:
                return {}

    async def send(self, text: str, callback_id: str) -> None:
        await self._post(_CREATE_AND_DELIVER,
                         build_card_payload(self.template_id, self.owner_id, callback_id, text))

    async def settle(self, callback_id: str, approved: bool) -> None:
        """点完刷终态。**失败只记日志不抛**——确认本身已经生效，不能因为刷不动卡片而翻车。"""
        try:
            await self._post(_UPDATE, build_update_payload(callback_id, approved))
        except Exception as e:  # noqa: BLE001
            _log.warning("卡片终态更新失败（确认已生效，不影响）：%s", str(e)[:160])
