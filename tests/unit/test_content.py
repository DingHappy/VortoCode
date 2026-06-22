"""多模态 content 块辅助测试（src/llm/content）——纯离线，不触网。"""

import base64

import pytest

from src.llm.content import (build_user_content, content_to_text, count_images,
                             image_block, is_image_ref)


def _png(tmp_path):
    # 最小合法 PNG（1x1）；内容无所谓，只验证读取+base64+MIME 流程
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    p = tmp_path / "x.png"
    p.write_bytes(raw)
    return p


def test_build_user_content_plain_is_str():
    assert build_user_content("hi", None) == "hi"          # 无图 → 向后兼容的纯字符串
    assert build_user_content("hi", []) == "hi"


def test_build_user_content_with_local_image(tmp_path):
    p = _png(tmp_path)
    c = build_user_content("看这张图", [str(p)])
    assert isinstance(c, list)
    assert c[0] == {"type": "text", "text": "看这张图"}
    assert c[1]["type"] == "image_url"
    assert c[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_user_content_empty_text_omits_text_block(tmp_path):
    p = _png(tmp_path)
    c = build_user_content("", [str(p)])
    assert all(b["type"] == "image_url" for b in c)        # 空文本不塞 text 块


def test_url_passthrough():
    blk = image_block("https://example.com/a.jpg")
    assert blk == {"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}}
    data = "data:image/png;base64,AAAA"
    assert image_block(data)["image_url"]["url"] == data


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        image_block("/no/such/file.png")


def test_content_to_text_and_count():
    blocks = [{"type": "text", "text": "前"},
              {"type": "image_url", "image_url": {"url": "data:..."}},
              {"type": "text", "text": "后"}]
    assert content_to_text(blocks) == "前[图片]后"          # base64 绝不出现在文本里
    assert count_images(blocks) == 1
    assert content_to_text("纯字符串") == "纯字符串"
    assert count_images("纯字符串") == 0


def test_is_image_ref():
    assert is_image_ref("a.png") and is_image_ref("b.JPEG")
    assert is_image_ref("data:image/png;base64,xx")
    assert is_image_ref("https://x/y.webp")
    assert not is_image_ref("a.txt") and not is_image_ref("")


def test_account_does_not_explode_on_image_content(monkeypatch, tmp_path):
    # 大 base64 图不能把估算撑爆：list content 只数文本+固定图成本
    from src.llm import client as C
    C.reset_usage()
    big = "data:image/png;base64," + ("A" * 200_000)
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"},
                                         {"type": "image_url", "image_url": {"url": big}}]}]
    C._account(msgs, "ok")                                  # 无 usage → 走估算分支
    u = C.get_usage()
    # 文本"hi"(~1) + 一张图(IMAGE_TOKEN_COST=1000) 量级，绝不是 base64 长度(20 万)级别
    assert u["prompt_tokens"] < 5000
