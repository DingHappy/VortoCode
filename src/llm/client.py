"""LLM 客户端 - OpenAI 兼容接口 / One API 网关"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 加载 .env 文件
try:
    from dotenv import load_dotenv

    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass


# 模型分级配置（可通过环境变量覆盖）
_DEFAULT_CHAT_MODEL = os.getenv("DEFAULT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
MODELS: Dict[str, Dict[str, str]] = {
    "cheap": {
        "model": os.getenv("LLM_MODEL_CHEAP", _DEFAULT_CHAT_MODEL),
        "description": "低成本模型，适合简单任务",
    },
    "balanced": {
        "model": os.getenv("LLM_MODEL_BALANCED", _DEFAULT_CHAT_MODEL),
        "description": "平衡模型，适合大多数任务",
    },
    "powerful": {
        "model": os.getenv("LLM_MODEL_POWERFUL", _DEFAULT_CHAT_MODEL),
        "description": "高性能模型，适合复杂任务",
    },
}


# 暂时性 HTTP 状态：值得重试（限流/网关/服务端抖动）。永久性的（400/401/403/404）立刻抛、不重试。
_TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}


# 各模型的**上下文窗口**（token）：让历史预算按模型自适应，而非死守一个保守值。
# 只登记「有把握」的公开模型；自有中转 mimo-* 的真实窗口不写死（避免猜错撑爆），
# 由用户经 env VORTOCODE_MODEL_CONTEXT_WINDOW 显式给（他清楚自己中转的上游窗口）。
# 前缀匹配（模型名常带日期/版本后缀），命中即取。
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4.1": 128_000,
    "gpt-4-turbo": 128_000,
    "o1": 128_000,
    "o3": 128_000,
    "claude-3.5": 200_000,
    "claude-3.7": 200_000,
    "claude-3-opus": 200_000,
    "claude-sonnet": 200_000,
    "claude-opus": 200_000,
    "claude": 200_000,
    "deepseek": 65_536,
    "qwen": 128_000,
    "gemini-1.5": 1_000_000,
    "gemini": 128_000,
}


def model_context_window(model: str) -> Optional[int]:
    """当前模型的上下文窗口（token）。优先 env 全局覆盖（自有中转按上游真实窗口配），
    否则按已知公开模型前缀匹配；都拿不到返回 None（调用方回退到保守默认）。"""
    env = os.getenv("VORTOCODE_MODEL_CONTEXT_WINDOW")
    if env:
        try:
            v = int(env)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    name = (model or "").strip().lower()
    if not name:
        return None
    for prefix, window in MODEL_CONTEXT_WINDOWS.items():
        if name.startswith(prefix) or prefix in name:
            return window
    return None


def _int_env(name: str, default: int) -> int:
    """读整型环境变量；缺省/坏值都回退到 default。"""
    try:
        return int(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 用量观测
# 进程级累计：所有真实 LLM 调用都过 chat/stream/_chat_with_requests，故这里能涵盖
# 主 agent + 所有子 agent 的总用量。优先用 API 精确值，拿不到时用估算（流式）。
# cached_tokens：命中上游 prompt 缓存的输入 token 数（若中转/上游支持自动前缀缓存则 >0）——
# 用来**验证缓存到底有没有在自有中转生效**（OpenAI 兼容协议下缓存是自动的、无需 cache_control）。
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

_USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}

# 按上下文（会话/回合）隔离的用量作用域：多会话服务端场景下，不同会话各记各的、互不串扰，
# 也不会因某会话 reset_usage 把所有人清零（旧版 _USAGE 是进程级全局）。默认 None → 回退全局
# _USAGE（TUI/CLI 单会话沿用旧行为、零改动）。contextvars 会随 create_task/gather/to_thread 传播，
# 故绑定一次即涵盖该回合的主 + 子 agent 全部 LLM 调用。
import contextvars

_usage_ctx: "contextvars.ContextVar" = contextvars.ContextVar("vc_usage", default=None)


def _cur_usage() -> Dict[str, int]:
    u = _usage_ctx.get()
    return u if u is not None else _USAGE


def _extract_cached_tokens(usage: Any) -> int:
    """从 usage 里抠出「命中缓存的输入 token」。兼容多家字段：
    OpenAI: prompt_tokens_details.cached_tokens；DeepSeek: prompt_cache_hit_tokens。拿不到记 0。"""
    if usage is None:
        return 0
    def _get(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
    details = _get(usage, "prompt_tokens_details")
    for src, key in ((details, "cached_tokens"), (usage, "prompt_cache_hit_tokens"),
                     (usage, "cached_tokens")):
        v = _get(src, key) if src is not None else None
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return 0


def new_usage() -> Dict[str, int]:
    """新建一个零初始化的用量计数器（供 bind_usage 绑定到某会话）。"""
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}


def bind_usage(scope: Dict[str, int]) -> None:
    """把**当前上下文**的用量计数绑定到给定 dict（每会话一份）。在回合任务内调用即隔离该回合计量。"""
    _usage_ctx.set(scope)


def _is_wide_char(c: str) -> bool:
    """宽字符（CJK/日文假名/谚文/兼容表意等）：粗估 ~1 token/字。

    旧版只覆盖 BMP 基本汉字（一..鿿 = U+4E00..U+9FFF），漏了扩展区/假名/谚文——
    中日韩混排会低估 token 数，进而让窗口预算失真。这里补齐常见宽字区段。
    """
    o = ord(c)
    return (0x3040 <= o <= 0x30FF        # 平假名 + 片假名
            or 0x3400 <= o <= 0x9FFF     # CJK 扩展 A + 基本
            or 0xAC00 <= o <= 0xD7A3     # 谚文音节
            or 0xF900 <= o <= 0xFAFF     # CJK 兼容表意
            or 0xFF00 <= o <= 0xFFEF     # 全角/半角形
            or 0x20000 <= o <= 0x3FFFF)  # CJK 扩展 B–G（星平面）


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK/假名/谚文等宽字 ~1 token，其余 ~4 字符/token。够用于窗口预算与用量提示。"""
    if not text:
        return 0
    wide = sum(1 for c in text if _is_wide_char(c))
    return max(1, wide + (len(text) - wide) // 4)


def add_usage(prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> None:
    u = _cur_usage()
    u["calls"] += 1
    u["prompt_tokens"] += int(prompt_tokens or 0)
    u["completion_tokens"] += int(completion_tokens or 0)
    u["total_tokens"] += int(prompt_tokens or 0) + int(completion_tokens or 0)
    u["cached_tokens"] = u.get("cached_tokens", 0) + int(cached_tokens or 0)   # 老作用域缺键也不炸


def get_usage() -> Dict[str, int]:
    return dict(_cur_usage())


def reset_usage() -> None:
    u = _cur_usage()
    for k in u:
        u[k] = 0


def _account(messages: List[Dict[str, str]], content: Optional[str], usage: Any = None) -> None:
    """记一次调用用量：有 API 精确 usage 就用，否则按文本估算。

    content 可能是内容块数组（多模态）：只数其中文本，图片/音频按固定成本估，
    绝不把 base64 当文本计入（否则估算会被撑爆）。
    """
    from src.llm.content import (AUDIO_TOKEN_COST, IMAGE_TOKEN_COST,
                                 content_to_text, count_audio, count_images)
    pt = ct = None
    cached = _extract_cached_tokens(usage)         # 命中缓存的输入 token（拿不到=0）
    if usage is not None:
        pt = getattr(usage, "prompt_tokens", None)
        ct = getattr(usage, "completion_tokens", None)
        if isinstance(usage, dict):
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if pt is None or ct is None:
        pt = sum(estimate_tokens(content_to_text(m.get("content", "")))
                 + count_images(m.get("content")) * IMAGE_TOKEN_COST
                 + count_audio(m.get("content")) * AUDIO_TOKEN_COST for m in messages)
        ct = estimate_tokens(content_to_text(content) if content is not None else "")
    add_usage(pt, ct, cached)


class LLMConfig(BaseModel):
    """LLM 配置"""

    base_url: str = Field(default_factory=lambda: os.getenv("OPENAI_API_BASE", DEFAULT_OPENAI_BASE_URL))
    api_key: str = ""
    # 默认模型读 .env 的 DEFAULT_MODEL/OPENAI_MODEL（之前写死 gpt-4o-mini，令牌无权会 403）
    model: str = Field(default_factory=lambda: os.getenv("DEFAULT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini")
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: float = 120.0
    # 上游 API 网关偶发 502/超时——SDK 默认重试 2 次，这里默认 3 且可经 OPENAI_MAX_RETRIES 调高。
    # 同时透传给 AsyncOpenAI（主路径）与 aiohttp 降级路径（原先零重试）。
    max_retries: int = Field(default_factory=lambda: _int_env("OPENAI_MAX_RETRIES", 3))
    retry_base_delay: float = 0.5      # 降级路径的退避基数（指数退避；测试可设 0 免真睡）


class LLMClient:
    """LLM 客户端 - OpenAI 兼容接口，带连接池复用"""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()

        # 从环境变量读取配置
        if not self.config.api_key:
            self.config.api_key = os.getenv("OPENAI_API_KEY", "")
        if not self.config.base_url:
            self.config.base_url = os.getenv(
                "OPENAI_API_BASE", DEFAULT_OPENAI_BASE_URL
            )

        self._client: Optional[Any] = None  # 懒加载 AsyncOpenAI 单例
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> Any:
        """获取或创建 AsyncOpenAI 单例（连接池复用）"""
        if self._client is not None:
            return self._client
        async with self._client_lock:
            # double-check
            if self._client is not None:
                return self._client
            try:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(
                    base_url=self.config.base_url,
                    api_key=self.config.api_key,
                    timeout=self.config.timeout,
                    max_retries=self.config.max_retries,   # 暂时性错误的内建退避重试（原先用 SDK 默认 2、不可调）
                )
            except ImportError:
                self._client = None
        return self._client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """发送聊天请求（异步，连接池复用）。

        给了 tools（OpenAI function schema 列表）则启用原生 function-calling，
        返回里多一个 tool_calls 字段（[{id,name,arguments}, ...] 或 None）。
        """
        client = await self._get_client()

        if client is None:
            # 无 openai 库时回退到 aiohttp（不支持原生 tools）
            return await self._chat_with_requests(
                messages, model, temperature, max_tokens
            )

        kwargs: Dict[str, Any] = dict(
            model=model or self.config.model,
            messages=messages,
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens,
            stream=stream,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = await client.chat.completions.create(**kwargs)

        if stream:
            return {"stream": response}

        message = response.choices[0].message
        # 推理型模型（如 DeepSeek-R1 系）会把思维链放在 reasoning_content/reasoning，
        # 普通模型没有该字段，getattr 取 None。这是推理链的源头。
        reasoning = getattr(message, "reasoning_content", None) or getattr(message, "reasoning", None)
        tool_calls = None
        if getattr(message, "tool_calls", None):
            tool_calls = [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                for tc in message.tool_calls
            ]
        _account(messages, message.content, getattr(response, "usage", None))
        return {
            "content": message.content,
            "reasoning": reasoning,
            "tool_calls": tool_calls,
            "model": response.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        }

    async def stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
    ):
        """异步逐块产出**正文**文本增量（用于流式展示）。

        on_reasoning：可选侧信道回调——推理型模型（DeepSeek-R1/mimo 等）的思维链增量
        （delta.reasoning_content）会走它，不混进正文 yield。通用：靠 getattr 探测，无则不触发。"""
        client = await self._get_client()

        if client is None:
            result = await self._chat_with_requests(
                messages, model, temperature, None
            )
            content = result.get("content", "")
            if content:
                yield content
            return

        create = dict(
            model=model or self.config.model,
            messages=messages,
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=self.config.max_tokens,
            stream=True,
        )
        # 优先请求精确 usage（最后一个 chunk 带 usage）；relay 不支持该参数就退回普通流式
        try:
            resp = await client.chat.completions.create(
                **create, stream_options={"include_usage": True})
        except Exception:  # noqa: BLE001
            resp = await client.chat.completions.create(**create)
        parts: List[str] = []
        exact = None
        async for chunk in resp:
            u = getattr(chunk, "usage", None)
            if u is not None:
                exact = u                       # include_usage 的尾 chunk
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is not None and on_reasoning is not None:   # 推理增量走侧信道（不混进正文）
                rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if rc:
                    try:
                        on_reasoning(rc)
                    except Exception:  # noqa: BLE001 —— 展示回调不该影响生成
                        pass
            content = getattr(delta, "content", None) if delta else None
            if content:
                parts.append(content)
                yield content
        _account(messages, "".join(parts), exact)   # 有精确 usage 用精确，否则估算

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        on_content: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """流式跑一次（可带 tools）对话，返回与 chat() 同形的 dict：{content, reasoning, tool_calls, model}。

        用于**原生 function-calling 的流式**：最终正文增量走 on_content（供 UI 边生成边回显），
        同时把分片的 tool_calls 跨 chunk 累积成完整列表。一旦出现 tool_call 增量就停止把后续正文喂给
        on_content——本次是"调工具"响应，附带碎语不该当最终回复回显（与提示式 _complete 的"疑似工具
        调用则抑制"对齐；也保证 CLI 的累计偏移只在最终步推进）。思维链增量走 on_reasoning，不混进正文。
        无 openai 库 → 回退非流式 chat（同样透传 tools），保证"模型无关"兜底不破。
        """
        client = await self._get_client()
        if client is None:                     # 无 openai 库：降级路径不支持流式/原生 tools
            return await self.chat(messages, model, temperature, tools=tools)

        create: Dict[str, Any] = dict(
            model=model or self.config.model,
            messages=messages,
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=self.config.max_tokens,
            stream=True,
        )
        if tools:
            create["tools"] = tools
            create["tool_choice"] = "auto"
        # 优先请求精确 usage（尾 chunk 带 usage）；relay 不支持该参数就退回普通流式
        try:
            resp = await client.chat.completions.create(
                **create, stream_options={"include_usage": True})
        except Exception:  # noqa: BLE001
            resp = await client.chat.completions.create(**create)

        parts: List[str] = []
        tc_acc: Dict[int, Dict[str, str]] = {}    # index -> {id,name,arguments}（分片累积）
        exact = None
        async for chunk in resp:
            u = getattr(chunk, "usage", None)
            if u is not None:
                exact = u                          # include_usage 的尾 chunk
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            if on_reasoning is not None:           # 推理增量走侧信道（不混进正文）
                rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if rc:
                    try:
                        on_reasoning(rc)
                    except Exception:  # noqa: BLE001 —— 展示回调不该影响生成
                        pass
            for tc in (getattr(delta, "tool_calls", None) or []):   # 组装 tool_calls
                idx = getattr(tc, "index", 0) or 0
                slot = tc_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] += fn.name
                    if getattr(fn, "arguments", None):
                        slot["arguments"] += fn.arguments
            content = getattr(delta, "content", None)
            if content:
                parts.append(content)
                if on_content is not None and not tc_acc:   # 出现工具调用后不再回显正文
                    try:
                        on_content(content)
                    except Exception:  # noqa: BLE001
                        pass
        full = "".join(parts)
        _account(messages, full, exact)            # 有精确 usage 用精确，否则估算
        tool_calls = None
        if tc_acc:
            tool_calls = [
                {"id": tc_acc[i].get("id") or "", "name": tc_acc[i]["name"],
                 "arguments": tc_acc[i]["arguments"]}
                for i in sorted(tc_acc)
            ]
        # reasoning 已经过 on_reasoning 增量给出，这里返 None，避免调用方再整段重放一次
        return {"content": full, "reasoning": None,
                "tool_calls": tool_calls, "model": model or self.config.model}

    async def _chat_with_requests(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """使用 aiohttp 发送请求（openai 库不可用时的降级方案）。

        对暂时性错误（_TRANSIENT_STATUS / 连接 / 超时）做指数退避重试，最多 max_retries 次——
        原先此路径零重试，上游 API 网关一次 502 就让整轮对话直接报错。永久性错误（400/401/403/404）立刻抛。
        """
        import aiohttp

        url = f"{self.config.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        payload = {
            "model": model or self.config.model,
            "messages": messages,
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }

        timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        last_err: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            if attempt:                                  # 重试前指数退避（首次 attempt=0 不睡）
                await asyncio.sleep(self.config.retry_base_delay * (2 ** (attempt - 1)))
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=payload, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            msg = data["choices"][0]["message"]
                            _account(messages, msg.get("content"), data.get("usage"))
                            return {
                                "content": msg["content"],
                                "reasoning": msg.get("reasoning_content") or msg.get("reasoning"),
                                "model": data.get("model", ""),
                                "usage": data.get("usage", {}),
                            }
                        error = await response.text()
                        err = Exception(f"LLM request failed: {response.status} - {error}")
                        if response.status in _TRANSIENT_STATUS:
                            last_err = err                # 暂时性 → 记下、重试
                            continue
                        raise err                         # 永久性 → 立刻抛、不浪费重试
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:   # 连接/超时也属暂时性
                last_err = e
                continue
        raise last_err or Exception("LLM request failed: 重试已用尽")

    async def tts(self, text: str, voice: Optional[str] = None,
                  model: Optional[str] = None) -> bytes:
        """文本转语音：返回 WAV 字节。

        兼容网关的 TTS 走 chat/completions——把要朗读的文本作为 **assistant 消息** 发给
        TTS 模型（默认 mimo-v2.5-tts，可用 TTS_MODEL 覆盖），音频在 message.audio.data（base64）。
        voice 可选（不同声音）。空文本或无音频返回会抛异常。
        """
        import base64

        import aiohttp
        text = (text or "").strip()
        if not text:
            raise ValueError("TTS 需要非空文本。")
        payload: Dict[str, Any] = {
            "model": model or os.getenv("TTS_MODEL", "mimo-v2.5-tts"),
            "messages": [{"role": "assistant", "content": text[:4000]}],   # 过长截断，避免超大请求
        }
        if voice:
            payload["voice"] = voice
        url = f"{self.config.base_url}/chat/completions"
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.config.api_key}"}
        timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    raise Exception(f"TTS 请求失败: {resp.status} - {(await resp.text())[:300]}")
                data = await resp.json()
        audio = (data.get("choices") or [{}])[0].get("message", {}).get("audio") or {}
        b64 = audio.get("data")
        if not b64:
            raise Exception("TTS 无音频返回（message.audio.data 为空）。")
        return base64.b64decode(b64)

    async def analyze(self, prompt: str, system_prompt: str = "") -> str:
        """分析任务"""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        result = await self.chat(messages)
        return result.get("content", "")

    async def close(self) -> None:
        """关闭客户端，释放连接池"""
        if self._client is not None:
            await self._client.close()
            self._client = None


# ---------------------------------------------------------------------------
# 模块级单例缓存（按 model_tier 复用）
# ---------------------------------------------------------------------------
_client_cache: Dict[str, LLMClient] = {}
_client_cache_lock = asyncio.Lock()


def get_llm_client(model_tier: str = "balanced") -> LLMClient:
    """获取 LLM 客户端（单例，按 tier 缓存）"""
    if model_tier in _client_cache:
        return _client_cache[model_tier]

    model_info = MODELS.get(model_tier, MODELS["balanced"])
    config = LLMConfig(
        base_url=os.getenv("OPENAI_API_BASE", DEFAULT_OPENAI_BASE_URL),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=model_info["model"],
    )
    client = LLMClient(config)
    _client_cache[model_tier] = client
    return client
