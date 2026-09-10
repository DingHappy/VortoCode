"""Telegram 入站附件：手机上发图/发文件，agent 收得到。

改之前 `_to_event` 只认 `msg["text"]`，而图片消息的字段叫 `photo`、正文叫 `caption`——
于是**整条消息走到 `return None` 被静默丢弃**：你发过去，bot 一声不吭，连"我不支持图片"
都不会说。这是 bridge 注释里点过名的老毛病（"发过去石沉大海是最差的体验"）。

bridge 早就通道无关地在消费 `ev.images`/`ev.files`/`ev.unsupported`，所以这里只测适配器
把这三个字段填对——不打真网，request_fn 与 download_fn 都注入假的。
"""
import asyncio
import os

import pytest

from src.im.telegram import _MAX_FILE_BYTES, TelegramAdapter, _attachment_refs, _to_event


def _adapter(tmp_path, *, files=None, fail=None, blob=b"BYTES"):
    """造一个不触网的适配器。files: file_id → file_path；fail: file_id → 异常。"""
    files = files or {}
    calls = []

    async def request_fn(method, payload):
        calls.append((method, payload))
        if method == "getFile":
            fid = payload["file_id"]
            if fail and fid in fail:
                raise fail[fid]
            return {"file_path": files.get(fid, f"photos/{fid}.jpg")}
        return {}

    async def download_fn(file_path):
        calls.append(("download", file_path))
        return blob

    a = TelegramAdapter("tok", "42", request_fn=request_fn,
                        inbox_dir=str(tmp_path / "inbox"), download_fn=download_fn)
    return a, calls


def _update(**msg):
    return {"update_id": 1, "message": {"from": {"id": 42}, "chat": {"type": "private"}, **msg}}


# ------------------------------------------------------------------ 认出附件（纯函数）
def test_photo_takes_the_largest_size():
    """Bot API 给同一张图的多档分辨率，按尺寸升序——取最后一个，别拿缩略图去做事。"""
    refs = _attachment_refs({"photo": [
        {"file_id": "thumb", "file_size": 100},
        {"file_id": "mid", "file_size": 5000},
        {"file_id": "full", "file_size": 90000},
    ]})
    assert refs == [("image", "full", "photo.jpg", 90000)]


@pytest.mark.parametrize("field, kind", [
    ("document", "file"), ("video", "file"), ("audio", "file"),
    ("voice", "file"), ("animation", "file"), ("sticker", "image"),
])
def test_every_attachment_field_is_recognised(field, kind):
    refs = _attachment_refs({field: {"file_id": "x", "file_size": 10}})
    assert refs and refs[0][0] == kind and refs[0][1] == "x"


def test_plain_text_has_no_attachments():
    assert _attachment_refs({"text": "只是说句话"}) == []
    assert _attachment_refs({}) == []


# ------------------------------------------------------------------ 消息不再被丢掉
def test_photo_only_message_still_becomes_an_event():
    """**这是原 bug 的直接反面**：只发一张图、一个字不写，改之前整条消息被静默丢弃。"""
    ev = _to_event(_update(photo=[{"file_id": "p1", "file_size": 900}]))
    assert ev is not None and ev.kind == "message" and ev.text == ""


def test_caption_is_used_as_the_body():
    """"图 + 一句说明"里说明在 caption 不在 text——只认 text 的话这句话也一起丢了。"""
    ev = _to_event(_update(photo=[{"file_id": "p1"}], caption="这个报错什么意思"))
    assert ev is not None and ev.text == "这个报错什么意思"


def test_service_messages_still_produce_nothing():
    """既没文字也没附件（入群通知之类）→ 照旧不产生事件，别把噪音喂给 agent。"""
    assert _to_event(_update(new_chat_members=[{"id": 7}])) is None


def test_group_mention_gate_reads_caption_entities():
    """群里"发图 @我"：提及信息在 caption_entities 而不是 entities。

    提及门是**从严**的（申报 is_group 却没申报 mentioned 就丢事件），所以不一并看的话，
    群里发图叫我永远叫不动。
    """
    up = {"update_id": 1, "message": {
        "from": {"id": 42}, "chat": {"type": "supergroup"},
        "photo": [{"file_id": "p1"}], "caption": "@mybot 看看这个",
        "caption_entities": [{"type": "mention", "offset": 0, "length": 6}]}}
    ev = _to_event(up, "mybot", "7")
    assert ev is not None and ev.is_group and ev.mentioned is True


# ------------------------------------------------------------------ 下载落盘
@pytest.mark.asyncio
async def test_image_and_file_land_in_the_right_buckets(tmp_path):
    a, calls = _adapter(tmp_path)
    imgs, files, note = await a._fetch_attachments([
        ("image", "p1", "photo.jpg", 100),
        ("file", "d1", "报告.pdf", 200),
    ])
    assert note == ""
    assert len(imgs) == 1 and len(files) == 1
    assert open(imgs[0], "rb").read() == b"BYTES"
    assert imgs[0].endswith(".jpg") and files[0].endswith("报告.pdf")
    assert ("getFile", {"file_id": "p1"}) in calls           # 两跳：先换 file_path 再取字节
    assert ("download", "photos/p1.jpg") in calls


@pytest.mark.asyncio
async def test_files_land_outside_the_repo_worktree(tmp_path):
    """收件落在专门目录，**不进仓库工作区**——否则用户发来的文件会被当成代码改动收进 diff。"""
    a, _ = _adapter(tmp_path)
    imgs, _f, _n = await a._fetch_attachments([("image", "p1", "photo.jpg", 10)])
    assert str(tmp_path / "inbox") in imgs[0]


@pytest.mark.asyncio
async def test_filename_from_the_user_cannot_escape_the_inbox(tmp_path):
    """用户发来的文件名是**外部输入**，直接拼进路径就是路径穿越。"""
    a, _ = _adapter(tmp_path)
    _i, files, _n = await a._fetch_attachments(
        [("file", "d1", "../../../../.ssh/authorized_keys", 10)])
    assert len(files) == 1
    assert str(tmp_path / "inbox") in os.path.realpath(files[0])
    assert ".." not in os.path.basename(files[0])


@pytest.mark.asyncio
async def test_photo_gets_an_extension_from_the_remote_path(tmp_path):
    """Telegram 对 photo 不给文件名，而下游读图靠后缀判类型——用远端 file_path 的后缀补上。"""
    a, _ = _adapter(tmp_path, files={"p1": "photos/abc.png"})
    imgs, _f, _n = await a._fetch_attachments([("image", "p1", "photo", 10)])
    assert imgs[0].endswith(".png")


# ------------------------------------------------------------------ 失败一律说出来
@pytest.mark.asyncio
async def test_oversize_is_refused_upfront_with_a_readable_reason(tmp_path):
    """超过 Bot API 的 20MB 硬限制：**提前如实说**，别发一次注定失败的请求再甩英文报错。"""
    a, calls = _adapter(tmp_path)
    imgs, files, note = await a._fetch_attachments(
        [("file", "big", "视频.mp4", _MAX_FILE_BYTES + 1)])
    assert not imgs and not files
    assert "20MB" in note and "视频.mp4" in note
    assert not any(m == "getFile" for m, _ in calls)          # 一次请求都没白发


@pytest.mark.asyncio
async def test_one_bad_attachment_does_not_sink_the_others(tmp_path):
    """单个附件失败不拖垮整条消息：其余照常交付，坏的那个如实点名。"""
    a, _ = _adapter(tmp_path, fail={"d1": RuntimeError("boom")})
    imgs, files, note = await a._fetch_attachments([
        ("image", "p1", "photo.jpg", 10),
        ("file", "d1", "坏的.pdf", 10),
    ])
    assert len(imgs) == 1 and not files
    assert "坏的.pdf" in note and "boom" in note


@pytest.mark.asyncio
async def test_empty_download_counts_as_a_failure(tmp_path):
    """下到 0 字节也是失败——落一个空文件下去，agent 读到的是"文件在但没内容"，更难查。"""
    a, _ = _adapter(tmp_path, blob=b"")
    imgs, files, note = await a._fetch_attachments([("image", "p1", "photo.jpg", 10)])
    assert not imgs and not files and "0 字节" in note


# ------------------------------------------------------------------ 端到端（poll）
@pytest.mark.asyncio
async def test_poll_fills_the_event_with_downloaded_paths(tmp_path):
    """bridge 是通道无关地读 ev.images/ev.files 的——适配器把这三个字段填对就够了。"""
    a, _ = _adapter(tmp_path)

    async def one_batch(method, payload):
        if method == "getUpdates":
            one_batch.done = getattr(one_batch, "done", False)
            if one_batch.done:
                raise asyncio.CancelledError
            one_batch.done = True
            return [_update(photo=[{"file_id": "p1", "file_size": 10}], caption="看这个")]
        if method == "getFile":
            return {"file_path": "photos/p1.jpg"}
        return {}

    a._request_fn = one_batch
    events = []
    with pytest.raises(asyncio.CancelledError):
        async for ev in a.poll():
            events.append(ev)
    assert len(events) == 1
    ev = events[0]
    assert ev.text == "看这个" and len(ev.images) == 1 and ev.unsupported == ""
    assert open(ev.images[0], "rb").read() == b"BYTES"


@pytest.mark.asyncio
async def test_text_only_message_downloads_nothing(tmp_path):
    """纯文本消息一次 getFile 都不该发——别给最常见的路径加一跳。"""
    a, calls = _adapter(tmp_path)
    ev = _to_event(_update(text="就说句话"))
    refs = _attachment_refs({"text": "就说句话"})
    assert ev is not None and refs == []
    assert not calls
