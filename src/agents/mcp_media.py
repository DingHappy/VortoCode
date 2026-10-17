"""Project MCP inline media into the existing tool image queue, never fetch URLs.

Raw transport results stay intact. Only known binary content fields are removed
from the text projection; structuredContent remains application data.
"""
from __future__ import annotations

import base64
import binascii
import json

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 1024 * 1024


def _image(data, mime):
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return None, 0, "unsupported_image_mime"
    if not isinstance(data, str) or not data:
        return None, 0, "invalid_image_data"
    if len(data) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        return None, 0, "image_size_limit"
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        return None, 0, "invalid_image_base64"
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None, 0, "image_size_limit"
    signatures = {
        "image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": raw.startswith(b"\xff\xd8\xff"),
        "image/gif": raw.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP",
    }
    if not signatures[mime]:
        return None, 0, "image_signature_mismatch"
    # Signature checks are not full decoding, pixel validation or vision proof.
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}", len(raw), "offered"


def render_result(output, structured=None):
    """Keep text-only JSON behavior; inline media uses the shared text/images shape."""
    images, receipts, projected = [], [], []
    total = 0
    if isinstance(output, list):
        for index, block in enumerate(output):
            if isinstance(block, dict) and block.get("type") in ("image", "audio"):
                item = {key: value for key, value in block.items() if key != "data"}
                item["data_omitted"] = True
                status = "unsupported_audio"
                if block["type"] == "image":
                    if len(images) >= MAX_IMAGES:
                        status = "image_count_limit"
                    else:
                        ref, size, status = _image(block.get("data"), block.get("mimeType"))
                        if ref and total + size > MAX_TOTAL_BYTES:
                            status = "image_total_size_limit"
                        elif ref:
                            images.append(ref)
                            total += size
                receipts.append({"index": index, "type": block["type"], "status": status})
                projected.append(item)
            elif (isinstance(block, dict) and block.get("type") == "resource"
                  and isinstance(block.get("resource"), dict) and "blob" in block["resource"]):
                resource = {key: value for key, value in block["resource"].items() if key != "blob"}
                resource["blob_omitted"] = True
                projected.append({**block, "resource": resource})
                receipts.append({"index": index, "type": "resource", "status": "unsupported_embedded_blob"})
            else:
                projected.append(block)
        output = projected
    if receipts:
        envelope = {"content": output, "mcp_media": receipts,
                    "detail": "图片仅交给原附件通道，仍受本批总上限与模型能力限制；请以实际附件为准。"
                              "未加载的媒体不代表已看见或听见；链接未自动读取。"}
        if structured is not None:
            envelope["structuredContent"] = structured
        return {"ok": True, "text": json.dumps(envelope, ensure_ascii=False, allow_nan=False), "images": images}
    if structured is not None:
        return json.dumps({"content": output, "structuredContent": structured}, ensure_ascii=False, allow_nan=False)
    return json.dumps(output, ensure_ascii=False, allow_nan=False) if isinstance(output, (list, dict)) else str(output)
