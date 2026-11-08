"""浏览器操控工具：Agent 在**独立浏览器**里打开网页、读页面、截图，经确认后点击和输入。

安全边界（改动前读完）：
- 独立配置目录、管道驱动，见 ``src/browser/cdp.py``——碰不到用户日常浏览器的登录态。
- **受限站点**：银行 / 支付 / 邮箱 / 密码管理 / 账号中心 / 云控制台拒绝 Agent 操作。
  专用会话中的 SSO 只能交还真人，不能被 Agent 读取。只能追加禁止域名，不能删减内置名单。
- **内网地址**：与 web_fetch 同一套 SSRF 判定；页面跳到内网后，读取和操作同样被拒并回到空白页。
- **点击 / 输入每次都问**：申报为 ``interact`` 类，任何授权档位都不免确认（见 agents/trust.py）。
  确认文案写明站点、元素和要输入的内容。
- 读到的页面内容是不可信外部内容：``untrusted_source`` + ``external_content``，摄入即给本回合
  打污点（D0 防提示注入）。
- 登录信息只留本机专用目录；密码/验证码交还真人。已识别敏感表单暂停读取和截图；
  不是覆盖任意页面秘密的检测器。登记、会话归属与手机网页合同见 docs/WEBSITE_CONNECTIONS.md。
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from src.agents.tool import Tool

# 内置受限站点：命中域名本身或其任意子域。
BLOCKED_DOMAINS = (
    # 支付 / 银行
    "paypal.com", "alipay.com", "tenpay.com", "pay.weixin.qq.com", "stripe.com", "wise.com",
    "icbc.com.cn", "ccb.com", "abchina.com", "boc.cn", "bankcomm.com", "cmbchina.com", "95559.com.cn",
    "chase.com", "bankofamerica.com", "wellsfargo.com", "citi.com", "hsbc.com", "hsbc.com.cn",
    # 邮箱
    "mail.google.com", "outlook.live.com", "outlook.office.com", "mail.qq.com", "exmail.qq.com",
    "mail.163.com", "mail.126.com", "mail.yahoo.com", "icloud.com",
    # 账号中心 / 密码管理
    "accounts.google.com", "appleid.apple.com", "login.live.com", "login.microsoftonline.com",
    "1password.com", "bitwarden.com", "lastpass.com", "dashlane.com",
    # 云控制台
    "console.aws.amazon.com", "signin.aws.amazon.com", "portal.azure.com",
    "console.cloud.google.com", "console.aliyun.com", "signin.aliyun.com", "console.cloud.tencent.com",
)
_KEYWORDS = ("bank",)   # 主机名里带 bank 的一律当银行处理

def browser_control_enabled(kind: str) -> bool:
    """只有 Desktop 托管的 Web 会话、且用户在设置里显式打开时才给浏览器操控。

    IM / CLI / 无人值守一律不给：IM 入口不可信，无人值守问不到人，而这组工具的意义就在
    "每一步由坐在电脑前的人确认"。
    """
    return (kind == "web" and os.getenv("VORTOONE_DESKTOP_SIDECAR") == "1"
            and os.getenv("VORTOONE_ENABLE_BROWSER_CONTROL") == "1")


def _blocked_domains() -> tuple[str, ...]:
    extra = [d.strip().lower().lstrip(".") for d in (os.getenv("VORTOONE_BROWSER_BLOCKED_DOMAINS") or "").split(",")]
    return BLOCKED_DOMAINS + tuple(d for d in extra if d)


def restricted_reason(url: str) -> str:
    """这个地址能不能碰；不能碰返回原因（一句人话），能碰返回空串。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "地址无法解析"
    if parsed.scheme == "about" and url in ("about:blank",):
        return ""
    if parsed.scheme not in ("http", "https"):
        return f"只允许 http/https 网页（拒绝 {parsed.scheme or '空'}）"
    if parsed.username is not None or parsed.password is not None:
        return "网址不能携带账号密码；请使用网站连接和人工登录"
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return "地址缺少主机名"
    for domain in _blocked_domains():
        if host == domain or host.endswith("." + domain):
            return f"{host} 属于受限站点（支付、银行、邮箱、账号或云控制台），浏览器操控不会碰它"
    if any(word in host for word in _KEYWORDS):
        return f"{host} 看起来是银行站点，浏览器操控不会碰它"
    from src.agents.web_fetch import refusal_reason
    reason = refusal_reason(host)
    return f"{reason}（浏览器操控不访问内网）" if reason else ""


def build_browser_tools(repo_root: str, confirm, *, browser_factory=None, connections=None, backend=None) -> list[Tool]:
    from src.agents.tools.website_browser import build_tools
    return build_tools(repo_root, confirm, browser_factory=browser_factory, connections=connections, backend=backend)
