"""多模态消息内容辅助：把图片/音频拼进 OpenAI 兼容的 content 块。

OpenAI/relay 的 chat message `content` 可以是纯字符串，也可以是内容块数组
`[{type:"text",...}, {type:"image_url",...}, {type:"input_audio",...}]`。本模块只负责
构造/解析这种结构，不依赖 LLMClient（避免循环导入）——传输层把 messages 原样透传即可。
mimo-v2.5 已实测可读图、可听音频（input_audio 块，无需单独 ASR）。
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

# 音频扩展名 → input_audio 的 format。实测中转站只支持 mp3/flac/m4a/wav/ogg
# （webm 会 400「invalid audio format」）——所以浏览器录音走客户端编码成 WAV，不发 webm。
_AUDIO_FMT = {
    ".mp3": "mp3", ".wav": "wav", ".m4a": "m4a", ".ogg": "ogg", ".flac": "flac",
}

# 估算用：图/音各按固定 token 计（真实用量以 API 返回为准；这里只让流式估算不至于离谱）。
IMAGE_TOKEN_COST = 1000
AUDIO_TOKEN_COST = 1500


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


def is_audio_ref(ref: str) -> bool:
    """ref 看起来像音频引用吗（data:audio/ URL，或已知音频扩展名的路径）。

    注意：input_audio 需要内联数据，不收 http(s) URL（与图片不同）。
    """
    r = (ref or "").strip()
    if not r:
        return False
    if r.startswith("data:audio/"):
        return True
    if r.startswith(("http://", "https://", "data:")):    # input_audio 需内联数据，不收远端 URL
        return False
    return Path(r).suffix.lower() in _AUDIO_FMT


def audio_block(ref: str) -> dict:
    """把一个音频引用变成 input_audio 内容块 {data: base64, format: mp3|wav|…}。

    - data:audio/<fmt>;base64,<...> → 解析出 format 与 base64。
    - 本地路径 → 读字节、base64，format 由扩展名定。
    缺文件/无法识别会抛异常，交由调用方给出友好提示。
    """
    r = (ref or "").strip()
    if r.startswith("data:audio/"):
        try:
            head, b64 = r.split(",", 1)
            fmt = head.split("/", 1)[1].split(";", 1)[0]
        except (ValueError, IndexError):
            raise ValueError(f"无法解析音频 data URL: {ref[:40]}…")
        return {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}
    p = Path(r).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"找不到音频文件: {ref}")
    fmt = _AUDIO_FMT.get(p.suffix.lower())
    if not fmt:
        raise ValueError(f"无法识别为音频（扩展名: {p.suffix or '无'}）: {ref}")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}


def build_user_content(text: str, images: Optional[list] = None,
                       audio: Optional[list] = None) -> Union[str, list]:
    """构造一条 user 消息的 content：无附件就返回纯字符串（保持向后兼容），

    有图/音则返回 [{type:text}, {type:image_url}…, {type:input_audio}…] 数组。
    images/audio 为路径或 URL（音频限本地/ data URL）的列表。
    """
    if not images and not audio:
        return text
    blocks: list = []
    if text:
        blocks.append({"type": "text", "text": text})
    for ref in (images or []):
        blocks.append(image_block(ref))
    for ref in (audio or []):
        blocks.append(audio_block(ref))
    return blocks


def content_to_text(content: Any) -> str:
    """从字符串或内容块数组里抽出纯文本（计量/截断/日志用，绝不带 base64）。

    图片/音频块不计文本；保留占位标记，便于日志看出"这条消息带了附件"。
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
            elif blk.get("type") == "input_audio":
                parts.append("[音频]")
        return "".join(parts)
    return str(content or "")


def count_images(content: Any) -> int:
    """内容块里有几张图（计量加成用）。"""
    if isinstance(content, list):
        return sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image_url")
    return 0


def count_audio(content: Any) -> int:
    """内容块里有几段音频（计量加成用）。"""
    if isinstance(content, list):
        return sum(1 for b in content if isinstance(b, dict) and b.get("type") == "input_audio")
    return 0
