"""长消息要**拆**，不是丢。

两个通道原先都是"超长就截断 + 加一句「已截断」"。截断的问题不在于人不知道被截了，
而在于**剩下的内容再也拿不到**：台账记着"已发送"，手机上只有前半截，后半截既不在聊天里
也不在别处。

真机 2026-09-10 用户反馈"钉钉消息过长会截断"，而它正好发生在刚加的产出物预览上——
**批之前该看见的内容，看不全**。那就不只是体验问题了。
"""
import pytest

from src.im.channel import MAX_CHUNKS, chunk_text


def test_short_text_is_not_touched():
    assert chunk_text("就一句话", 100) == ["就一句话"]


def test_empty_text_yields_nothing():
    assert chunk_text("", 100) == []


def test_nothing_is_lost_across_the_split():
    """**这条是本改动的全部意义。** 拼回去要能还原原文（除去分片标记与首尾空白）。"""
    body = "\n\n".join(f"第{i}段：" + "内容" * 30 for i in range(1, 12))
    parts = chunk_text(body, 400, max_chunks=99)
    joined = "".join(p.split("\n（")[0] for p in parts)
    assert joined.replace("\n", "").replace(" ", "") == body.replace("\n", "").replace(" ", "")


def test_every_part_fits_the_limit():
    parts = chunk_text("字" * 5000, 400, max_chunks=99)
    assert parts and all(len(p) <= 400 for p in parts)


def test_parts_are_numbered_so_you_know_if_one_is_missing():
    parts = chunk_text("字" * 2000, 400)
    assert "（1/" in parts[0] and f"/{len(parts)}）" in parts[-1]


def test_it_prefers_paragraph_boundaries_over_cutting_mid_sentence():
    body = "第一段" + "甲" * 200 + "\n\n" + "第二段" + "乙" * 200
    parts = chunk_text(body, 260, max_chunks=99)
    assert parts[0].startswith("第一段") and "乙" not in parts[0]


def test_overflow_says_how_much_did_not_get_sent():
    """超上限时**说清还剩多少**——"发完了"和"发了一部分"必须能区分开。"""
    parts = chunk_text("字" * 100_000, 400)
    assert len(parts) == MAX_CHUNKS
    assert "还有约" in parts[-1] and "没发" in parts[-1]


# ------------------------------------------------------------------ 通道真的分片发了吗
@pytest.mark.asyncio
async def test_dingtalk_sends_every_part(monkeypatch):
    from src.im.dingtalk import DingTalkAdapter

    sent = []
    a = DingTalkAdapter("cid", "secret", "owner-1")
    a._webhook = "https://example/hook"

    async def reply(url, payload):
        sent.append(payload["text"]["content"])

    a._reply_fn = reply
    await a.send_text("字" * 9000)
    assert len(sent) >= 3                       # 拆开了
    assert all(len(p) <= 4000 for p in sent)
    assert "已截断" not in "".join(sent)         # 不再是丢弃语义


@pytest.mark.asyncio
async def test_telegram_sends_every_part_and_returns_the_last_id(tmp_path):
    from src.im.telegram import TelegramAdapter

    sent = []

    async def request_fn(method, payload):
        sent.append(payload.get("text", ""))
        return {"message_id": len(sent)}

    a = TelegramAdapter("tok", "42", request_fn=request_fn, inbox_dir=str(tmp_path))
    mid = await a.send_text("字" * 9000)
    assert len(sent) >= 3
    # 进度条要接着往下编辑最后一条——指向第一条会把后面几条晾在那儿
    assert mid == str(len(sent))
