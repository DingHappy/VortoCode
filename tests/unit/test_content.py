"""多模态 content 块辅助测试（src/llm/content）——纯离线，不触网。"""

import base64

import pytest

from src.llm.content import (audio_block, build_user_content, content_to_text,
                             count_audio, count_images, image_block, is_audio_ref,
                             is_image_ref)


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


# ---- 音频 ----

def _wav(tmp_path):
    import base64
    # 极小 WAV 头（44 字节，无样本）；只验证读取+base64+format 流程
    raw = base64.b64decode("UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=")
    p = tmp_path / "clip.wav"
    p.write_bytes(raw)
    return p


def test_audio_block_local_file(tmp_path):
    p = _wav(tmp_path)
    blk = audio_block(str(p))
    assert blk["type"] == "input_audio"
    assert blk["input_audio"]["format"] == "wav"
    assert len(blk["input_audio"]["data"]) > 10            # base64 内联


def test_audio_block_data_url():
    blk = audio_block("data:audio/mp3;base64,QUJD")
    assert blk == {"type": "input_audio", "input_audio": {"data": "QUJD", "format": "mp3"}}


def test_audio_missing_and_bad_ext(tmp_path):
    with pytest.raises(FileNotFoundError):
        audio_block("/no/such.mp3")
    bad = tmp_path / "x.txt"
    bad.write_text("hi")
    with pytest.raises(ValueError):
        audio_block(str(bad))                              # 非音频扩展名


def test_build_user_content_audio_and_mixed(tmp_path):
    p = _wav(tmp_path)
    c = build_user_content("转写", audio=[str(p)])
    assert c[0] == {"type": "text", "text": "转写"} and c[1]["type"] == "input_audio"
    # 图 + 音混合：顺序为 text, image_url..., input_audio...
    img = _png(tmp_path)
    m = build_user_content("看图听音", images=[str(img)], audio=[str(p)])
    assert [b["type"] for b in m] == ["text", "image_url", "input_audio"]
    assert content_to_text(m) == "看图听音[图片][音频]"
    assert count_audio(m) == 1 and count_images(m) == 1


def test_is_audio_ref():
    assert is_audio_ref("a.mp3") and is_audio_ref("b.WAV")
    assert is_audio_ref("data:audio/wav;base64,xx")
    assert not is_audio_ref("a.png") and not is_audio_ref("https://x/y.mp3")  # input_audio 不收 http
    assert not is_audio_ref("")


def test_account_counts_audio(tmp_path):
    from src.llm import client as C
    C.reset_usage()
    big = "Z" * 300_000
    msgs = [{"role": "user", "content": [{"type": "text", "text": "听"},
                                         {"type": "input_audio", "input_audio": {"data": big, "format": "mp3"}}]}]
    C._account(msgs, "ok")
    u = C.get_usage()
    assert u["prompt_tokens"] < 6000                        # 不被 base64 长度撑爆，按固定音频成本计
