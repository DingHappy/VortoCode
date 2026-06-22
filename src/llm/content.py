"""多模态消息内容辅助：把图片拼进 OpenAI 兼容的 content 块（mimo-v2.5 已验证可读图）。

OpenAI/relay 的 chat message `content` 可以是纯字符串，也可以是内容块数组
`[{type:"text",...}, {type:"image_url",...}]`。本模块只负责构造/解析这种结构，
不依赖 LLMClient（避免循环导入）——传输层把 messages 原样透传即可。
"""

import base64
import mimetypes
from pathlib import Path
from typing import Any, Optional, Union

# 常见可读图扩展名 → MIME（guess_type 兜不住时用）
_IMG_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}

# 估算用：一张图按固定 token 计（真实用量以 API 返回为准；这里只让流式估算不至于离谱）。
IMAGE_TOKEN_COST = 1000


def is_image_ref(ref: str) -> bool:
    """ref 看起来像图片引用吗（data: URL、http(s) URL，或已知图片扩展名的路径）。"""
    r = (ref or "").strip()
    if not r:
        return False
    if r.startswith(("data:image/", "http://", "https://")):
        return True
    return Path(r).suffix.lower() in _IMG_MIME


def image_block(ref: str) -> dict:
    """把一个图片引用变成 image_url 内容块。

    - data:/http(s): URL → 原样透传（远端图由服务端取，data URL 已内联）。
    - 本地路径 → 读字节、base64、按扩展名定 MIME，拼成 data URL 内联。
    缺文件/读失败会抛异常，交由调用方给出友好提示。
    """
    r = (ref or "").strip()
    if r.startswith(("data:", "http://", "https://")):
        return {"type": "image_url", "image_url": {"url": r}}
    p = Path(r).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"找不到图片文件: {ref}")
    mime = mimetypes.guess_type(str(p))[0] or _IMG_MIME.get(p.suffix.lower())
    if not mime or not mime.startswith("image/"):
        raise ValueError(f"无法识别为图片（扩展名: {p.suffix or '无'}）: {ref}")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def build_user_content(text: str, images: Optional[list] = None) -> Union[str, list]:
    """构造一条 user 消息的 content：无图片就返回纯字符串（保持向后兼容），

    有图片则返回 [{type:text}, {type:image_url}...] 数组。images 可为路径或 URL 的列表。
    """
    if not images:
        return text
    blocks: list = []
    if text:
        blocks.append({"type": "text", "text": text})
    for ref in images:
        blocks.append(image_block(ref))
    return blocks


def content_to_text(content: Any) -> str:
    """从字符串或内容块数组里抽出纯文本（计量/截断/日志用，绝不带 base64）。

    图片块不计文本；保留一个占位标记，便于日志看出"这条消息带了图"。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if not isinstance(blk, dict):
                parts.append(str(blk))
            elif blk.get("type") == "text":
                parts.append(str(blk.get("text", "")))
            elif blk.get("type") == "image_url":
                parts.append("[图片]")
        return "".join(parts)
    return str(content or "")


def count_images(content: Any) -> int:
    """内容块里有几张图（计量加成用）。"""
    if isinstance(content, list):
        return sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image_url")
    return 0
