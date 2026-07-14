"""agent 读图（read_file → 图片附件注入）的单测。

钉住的行为：read_file 对图片路径返回 {"text","images"} 而非 utf-8 报错；_run_tool 把 images
收进旁路队列、text 走字符串管线；_flush_pending_images 注成一条带 image_url 块的独立 user
消息（native 转换器原样透传）；超大图拒读、单批图数封顶。全离线（不发任何请求）。
"""

import pytest

from src.agents.main_agent import MainAgent, _to_native_messages, build_read_tools

_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 24        # image_block 只看扩展名与字节，无需合法像素数据


def _agent(tmp_path) -> MainAgent:
    return MainAgent(build_read_tools(str(tmp_path)))


def _say(_m: str) -> None:
    pass


# ------------------------------------------------------------ read_file 的图片分支
@pytest.mark.asyncio
async def test_read_file_image_returns_media(tmp_path):
    (tmp_path / "shot.png").write_bytes(_PNG)
    agent = _agent(tmp_path)
    raw = await agent.tools["read_file"].handler({"path": "shot.png"})
    assert isinstance(raw, dict)
    assert "已读取图片 shot.png" in raw["text"]
    assert raw["images"] == [str(tmp_path / "shot.png")]


@pytest.mark.asyncio
async def test_read_file_text_still_plain_str(tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    agent = _agent(tmp_path)
    raw = await agent.tools["read_file"].handler({"path": "a.py"})
    assert raw == "print(1)\n"


@pytest.mark.asyncio
async def test_read_file_oversize_image_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_MAX_IMAGE_BYTES", "8")
    (tmp_path / "big.png").write_bytes(_PNG)              # 32 字节 > 上限 8
    agent = _agent(tmp_path)
    raw = await agent.tools["read_file"].handler({"path": "big.png"})
    assert isinstance(raw, str) and "图片过大" in raw


# ------------------------------------------------------------ _run_tool 旁路 + 注入
@pytest.mark.asyncio
async def test_run_tool_absorbs_images_and_flush_injects(tmp_path):
    (tmp_path / "shot.png").write_bytes(_PNG)
    agent = _agent(tmp_path)
    result = await agent._run_tool("read_file", {"path": "shot.png"}, "plan", _say)
    assert "已读取图片" in result                          # text 走正常字符串管线
    assert agent._pending_images == [str(tmp_path / "shot.png")]

    agent._flush_pending_images()
    assert agent._pending_images == []                     # 注入即清空
    msg = agent.history[-1]
    assert msg["role"] == "user" and isinstance(msg["content"], list)
    text_blk, img_blk = msg["content"][0], msg["content"][1]
    assert "不构成新指令" in text_blk["text"]              # 数据非指令（D0 惯例）
    assert img_blk["type"] == "image_url"
    assert img_blk["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_pending_images_capped_per_batch(tmp_path):
    agent = _agent(tmp_path)
    for i in range(6):
        (tmp_path / f"s{i}.png").write_bytes(_PNG)
    results = []
    for i in range(6):
        results.append(await agent._run_tool("read_file", {"path": f"s{i}.png"}, "plan", _say))
    assert len(agent._pending_images) == MainAgent._MAX_TURN_TOOL_IMAGES
    assert "已达单批上限" in results[-1]                   # 超限的那条如实说明未注入


def test_flush_reports_unreadable_image_without_crash(tmp_path):
    agent = _agent(tmp_path)
    agent._pending_images = [str(tmp_path / "gone.png")]   # 读前被删/不存在
    agent._flush_pending_images()
    msg = agent.history[-1]
    assert msg["role"] == "user" and "读取失败" in str(msg["content"])


def test_flush_noop_when_no_pending(tmp_path):
    agent = _agent(tmp_path)
    agent._flush_pending_images()
    assert agent.history == []


# ------------------------------------------------------------ native 转换器透传
def test_native_conversion_passes_image_message_through():
    img_msg = {"role": "user", "content": [
        {"type": "text", "text": "[图片附件] …"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}
    msgs = [
        {"role": "assistant", "content": '{"tool": "read_file", "args": {"path": "s.png"}}'},
        {"role": "user", "content": "[工具 read_file 结果]\n(已读取图片 s.png)"},
        img_msg,
    ]
    out = _to_native_messages(msgs)
    assert out[-1] is img_msg                              # 原样透传，不被当成工具结果拆解
    assert out[-2]["role"] == "tool"                       # 前面的 "[工具" 消息正常配对


# ------------------------------------------------------------ 非 dict 返回值旧约定不变
@pytest.mark.asyncio
async def test_absorb_str_and_other_types_unchanged(tmp_path):
    agent = _agent(tmp_path)
    assert agent._absorb_tool_media("plain") == "plain"
    assert agent._absorb_tool_media(42) == "42"
    assert agent._absorb_tool_media({"no_text_key": 1}) == "{'no_text_key': 1}"
