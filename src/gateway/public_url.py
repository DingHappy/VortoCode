"""PWA 深链（P4）：把「通知在钉钉，富界面在 PWA」这两半接起来。

`VORTOCODE_PUBLIC_BASE_URL` = 手机能打开的控制台地址（家用局域网 `http://192.168.x.x:8080`，
出门用 Tailscale 主机名）。**默认不设 = 完全不启用**，所有消息与今天逐字节相同——
和卡片模板同一条纪律：新能力锁在配置门后面，配错世界也不变坏。

刻意只认 http/https：钉钉消息里的链接会被点，`javascript:` / `file:` 这类 scheme
从配置渗进消息就是给未来的自己埋雷（配置文件也可能被别的工具写坏）。
"""
from __future__ import annotations

import os


def public_base_url() -> str:
    """配置的公共基址；未配置/不合法 → 空串（= 不加链接）。"""
    raw = os.getenv("VORTOCODE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not raw or not raw.lower().startswith(("http://", "https://")):
        return ""
    return raw


def agent_link() -> str:
    """控制台深链的展示行；未配置 → 空串（调用方直接拼接，无需判空再加换行）。"""
    base = public_base_url()
    return f"\n📱 {base}/agent" if base else ""
