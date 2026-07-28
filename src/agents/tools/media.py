"""出站媒体：把图片/文件推给**已配对的 owner**。

收件人恒为 owner、模型不能指定目标——这是它与 web_fetch 的本质区别（后者 URL 由模型决定，
是真外传通道；这里目标固定，等同于"回话给主人"）。
"""

from __future__ import annotations

from typing import Callable, Optional

from src.agents.tool import Tool
from src.agents.tools.files import _resolve_within


def build_im_media_tools(repo_root: str, confirm: Optional[Callable] = None) -> list[Tool]:
    """把图片/文件推给**已配对的 owner**（目前钉钉/Telegram）。

    存在的理由：agent 已经能无头截网页、把 Word/PPT 转成图，但交付通道是断的——图片只能落盘，
    人在手机上看不到。审批一段长 diff 时，一张渲染好的图远比一大段文本可读。

    安全口径（与确认门内核一致，不另起一套）：
    - **收件人恒为已配对 owner**，模型不能指定目标。这是它与 web_fetch 的本质区别：
      后者 URL 由模型决定，是真外传通道；这里目标固定，等同于"回话给主人"。
    - 路径必须过 `_resolve_within` 围栏，只能发工作目录内的文件。
    - **走 confirm 门**：干净回合下 gate 判 ALLOW 即静默放行；**污点回合下 gate 会要求人批**
      ——被注入的 agent 可能被诱导"把 .env 截个图发出去"，那一步必须有人点头。
    - 无人值守档不装这个工具（同 with_web=False 的道理：没有真人可问，出站面一律砍掉）。
    """
    from pathlib import Path

    base = Path(repo_root).resolve()

    async def _send(args: dict, kind: str) -> str:
        rel = str(args.get("path") or "").strip()
        caption = str(args.get("caption") or args.get("note") or "").strip()
        if not rel:
            return f"send_{kind} 需要 path（要发送的{'图片' if kind == 'image' else '文件'}路径）。"
        p = _resolve_within(base, rel)
        if p is None or not p.is_file():
            return f"路径越界或文件不存在: {rel}"
        if confirm is not None:
            ok = await confirm(f"把{'图片' if kind == 'image' else '文件'}「{p.name}」发送给主人的 IM？"
                               f"（{p.stat().st_size // 1024} KB）")
            if not ok:
                return "已取消：用户未放行本次发送。"
        from src.gateway.im_runtime import send_owner_media
        sent = await send_owner_media(str(p), caption, kind)
        return (f"✅ 已发送 {p.name} 到 IM。" if sent
                else "未发送：当前没有已连接的 IM 桥，或该通道不支持发媒体（内容仍在原路径）。")

    return [
        Tool("send_image", "把一张图片发到主人的 IM（钉钉/Telegram）。用于把截图、渲染好的 diff、"
                           "文档页面图直接送到手机上——比让人去服务器上翻文件强得多。",
             {"path": "仓库内的图片路径", "caption": "可选：随图附一句说明"},
             lambda a: _send(a, "image")),
        Tool("send_file", "把一个文件发到主人的 IM（钉钉/Telegram）。适合日志、报告、导出的数据。",
             {"path": "仓库内的文件路径", "caption": "可选：随文件附一句说明"},
             lambda a: _send(a, "file")),
    ]
