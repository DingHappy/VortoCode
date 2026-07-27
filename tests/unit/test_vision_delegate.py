"""模态兜底：主模型吃不下图片时，把"看图"外包给视觉模型，再把描述喂回主模型。

真机 2026-07-27：`mimo-v2.5-pro` 无视觉（中转站原话 `No endsupport image input`，HTTP 404），
而 `mimo-v2.5` 能准确读图——同一个中转站里就有互补能力，没理由让整条链路因为主模型的一个
短板而瘫掉。

四条纪律（都容易被后续重构悄悄破坏，所以逐条钉死）：
1. **只认"不支持图片"这一种错误**去降级；网络抖动/限流不许被当成"该外包了"——
   用错误的方式掩盖错误比原错误更难查。
2. **必须标注来源**：描述是有损的二手信息，主模型不该把它当亲眼所见。
3. **外包失败不静默**：换成写明原因的占位文字，而不是让图凭空消失。
4. **没图就别绕路**：纯文本请求不该被这套逻辑碰。
"""
import pytest

from src.llm.client import (LLMClient, LLMConfig, _append_note, _is_no_image_support,
                            _split_images)

_IMG = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}


def _msgs():
    return [{"role": "user", "content": [{"type": "text", "text": "这是什么？"}, dict(_IMG)]}]


# ---------------------------------------------------------------- 错误判别（降级的触发条件）

@pytest.mark.parametrize("msg", [
    "No endpoints found that support image input",
    "Error code: 404 - {'error': {'message': 'No endpoints found that support image input'}}",
])
def test_recognizes_no_image_support(msg):
    assert _is_no_image_support(RuntimeError(msg))


@pytest.mark.parametrize("msg", [
    "502 Bad Gateway",
    "Connection reset by peer",
    "rate limit exceeded",
    "Request timed out",
    "context length exceeded",
])
def test_does_not_mistake_other_failures_for_missing_vision(msg):
    """把可恢复故障误判成"不支持图片"→ 会用外包掩盖真正的通道问题，死因又一次被改写。"""
    assert not _is_no_image_support(RuntimeError(msg))


# ---------------------------------------------------------------- 图片剥离

def test_split_images_extracts_and_keeps_text():
    stripped, imgs = _split_images(_msgs())
    assert imgs == ["data:image/png;base64,AAA"]
    assert stripped[0]["content"] == [{"type": "text", "text": "这是什么？"}]


def test_split_images_leaves_plain_text_untouched():
    msgs = [{"role": "user", "content": "纯文本"}]
    stripped, imgs = _split_images(msgs)
    assert imgs == [] and stripped == msgs


def test_append_note_lands_on_last_user_message():
    out = _append_note([{"role": "user", "content": "问题"}], "[说明]")
    assert out[-1]["content"].endswith("[说明]")
    assert "问题" in out[-1]["content"]


# ---------------------------------------------------------------- 外包行为

class _FakeChat:
    def __init__(self, outer):
        self.completions = outer


class _FakeClient:
    """假 OpenAI 客户端：主模型对图片报 404，视觉模型正常返回描述。"""

    def __init__(self, vision_reply="图上写着 紫色鲸鱼 7392", vision_raises=None):
        self.chat = _FakeChat(self)
        self.calls: list = []
        self._vision_reply = vision_reply
        self._vision_raises = vision_raises

    async def create(self, **kw):
        self.calls.append(kw)
        model = kw["model"]
        has_img = bool(_split_images(kw["messages"])[1])
        if model == "vision-m":
            if self._vision_raises:
                raise self._vision_raises
            return _resp(self._vision_reply)
        if has_img:
            raise RuntimeError("Error code: 404 - No endpoints found that support image input")
        return _resp("主模型答复")


def _resp(content):
    class _M:
        def __init__(self, c):
            self.content = c
            self.tool_calls = None
    class _C:
        def __init__(self, c):
            self.message = _M(c)
    class _U:                       # 真实响应带 usage，缺了会在计量处炸
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15
    class _R:
        def __init__(self, c):
            self.choices = [_C(c)]
            self.model = "x"
            self.usage = _U()
    return _R(content)


def _client(monkeypatch, fake):
    c = LLMClient(LLMConfig(model="blind-m", vision_model="vision-m", api_key="k"))

    async def _get(*a, **k):
        return fake
    monkeypatch.setattr(c, "_get_client", _get)
    return c


async def test_delegates_to_vision_model_and_labels_the_source(monkeypatch):
    """外包成功：描述进了主模型的上下文，且**明说是二手信息**。"""
    fake = _FakeClient()
    out = await _client(monkeypatch, fake).chat(_msgs())

    assert out["content"] == "主模型答复"
    # 三次调用：主模型撞 404 → 视觉模型描述 → 主模型带描述重试
    assert [c["model"] for c in fake.calls] == ["blind-m", "vision-m", "blind-m"]

    final_text = str(fake.calls[-1]["messages"])
    assert "紫色鲸鱼 7392" in final_text                 # 描述确实喂进去了
    assert "你没有直接看到原图" in final_text            # **标注来源**，别让它当亲眼所见
    assert not _split_images(fake.calls[-1]["messages"])[1], "重试时还带着图片，等于没剥干净"


async def test_second_call_skips_the_wasted_404(monkeypatch):
    """同一模型第二次带图请求，不该再白撞一次 404。"""
    from src.llm import client as mod

    mod._NO_VISION_MODELS.discard("blind-m")
    fake = _FakeClient()
    c = _client(monkeypatch, fake)
    await c.chat(_msgs())
    n_first = len(fake.calls)
    await c.chat(_msgs())
    # 第二轮只该有两次：视觉模型 + 主模型（没有那次注定失败的探路）
    assert len(fake.calls) - n_first == 2
    mod._NO_VISION_MODELS.discard("blind-m")


async def test_vision_failure_degrades_with_reason_not_silence(monkeypatch):
    """视觉模型也挂了 → 留下写明原因的占位，不让图凭空消失。"""
    from src.llm import client as mod

    mod._NO_VISION_MODELS.discard("blind-m")
    fake = _FakeClient(vision_raises=RuntimeError("503 upstream down"))
    await _client(monkeypatch, fake).chat(_msgs())

    final_text = str(fake.calls[-1]["messages"])
    assert "图片未能读取" in final_text and "503" in final_text
    mod._NO_VISION_MODELS.discard("blind-m")


async def test_no_vision_model_configured_says_so(monkeypatch):
    """没配视觉模型 → 如实说明缺什么，而不是假装图不存在。"""
    from src.llm import client as mod

    mod._NO_VISION_MODELS.discard("blind-m")
    fake = _FakeClient()
    c = LLMClient(LLMConfig(model="blind-m", vision_model="", api_key="k"))

    async def _get(*a, **k):
        return fake
    monkeypatch.setattr(c, "_get_client", _get)
    await c.chat(_msgs())

    final_text = str(fake.calls[-1]["messages"])
    assert "VORTOCODE_VISION_MODEL" in final_text
    mod._NO_VISION_MODELS.discard("blind-m")


async def test_plain_text_never_takes_the_fallback_path(monkeypatch):
    """纯文本请求不该被这套逻辑碰——一次多余调用都不许有。"""
    fake = _FakeClient()
    out = await _client(monkeypatch, fake).chat([{"role": "user", "content": "你好"}])
    assert out["content"] == "主模型答复"
    assert [c["model"] for c in fake.calls] == ["blind-m"]
