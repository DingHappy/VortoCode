"""钉钉 Webhook 通知 Hook

在任务完成或出错时通过钉钉机器人推送消息。
"""

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Optional

from ..hook import Hook, HookEvent, HookEventType, HookResult

logger = logging.getLogger(__name__)


class DingTalkHook(Hook):
    """钉钉 webhook 通知"""

    def __init__(self, webhook_url: str, secret: str = ""):
        super().__init__(
            name="dingtalk_notification",
            event_types=[HookEventType.TASK_END, HookEventType.ERROR],
            priority=10,
        )
        self.webhook_url = webhook_url
        self.secret = secret

    def _sign(self) -> str:
        """钉钉加签"""
        if not self.secret:
            return ""
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"&timestamp={timestamp}&sign={sign}"

    async def execute(self, event: HookEvent) -> HookResult:
        """发送钉钉通知"""
        stage = event.data.get("stage", event.source or "unknown")
        report = event.data.get("report", event.data.get("error", ""))

        if event.event_type == HookEventType.TASK_END:
            title = f"[量化平台] {stage} 完成"
            color = "green"
        else:
            title = f"[量化平台] {stage} 异常"
            color = "red"

        text_content = f"## {title}\n\n{str(report)[:2000]}"

        message = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text_content},
        }

        try:
            import aiohttp

            url = self.webhook_url + self._sign()
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=message) as resp:
                    if resp.status != 200:
                        logger.warning("DingTalk webhook returned %d", resp.status)
                    else:
                        logger.info("DingTalk notification sent: %s", title)
        except Exception as e:
            logger.error("Failed to send DingTalk notification: %s", e)
            return HookResult(success=False, error=str(e))

        return HookResult(success=True)


def create_dingtalk_hook(
    webhook_url: str, secret: str = ""
) -> Optional[DingTalkHook]:
    """工厂函数：创建钉钉 Hook（webhook_url 为空则跳过）"""
    if not webhook_url:
        logger.warning("DingTalk webhook URL not configured, skipping")
        return None
    return DingTalkHook(webhook_url=webhook_url, secret=secret)
