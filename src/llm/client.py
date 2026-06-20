"""LLM 客户端 - 集成 One API 网关"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

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


# 模型分级配置
# 模型分级配置（可通过环境变量覆盖）
MODELS: Dict[str, Dict[str, str]] = {
    "cheap": {
        "model": os.getenv("LLM_MODEL_CHEAP", "mimo-v2.5"),
        "description": "低成本模型，适合简单任务",
    },
    "balanced": {
        "model": os.getenv("LLM_MODEL_BALANCED", "mimo-v2.5"),
        "description": "平衡模型，适合大多数任务",
    },
    "powerful": {
        "model": os.getenv("LLM_MODEL_POWERFUL", "mimo-v2.5-pro"),
        "description": "高性能模型，适合复杂任务",
    },
}


# ---------------------------------------------------------------- 用量观测
# 进程级累计：所有真实 LLM 调用都过 chat/stream/_chat_with_requests，故这里能涵盖
# 主 agent + 所有子 agent 的总用量。优先用 API 精确值，拿不到时用估算（流式）。
_USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 字 ~1 token，其余 ~4 字符/token。够用于用量提示。"""
    if not text:
        return 0
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return max(1, cjk + (len(text) - cjk) // 4)


def add_usage(prompt_tokens: int, completion_tokens: int) -> None:
    _USAGE["calls"] += 1
    _USAGE["prompt_tokens"] += int(prompt_tokens or 0)
    _USAGE["completion_tokens"] += int(completion_tokens or 0)
    _USAGE["total_tokens"] += int(prompt_tokens or 0) + int(completion_tokens or 0)


def get_usage() -> Dict[str, int]:
    return dict(_USAGE)


def reset_usage() -> None:
    for k in _USAGE:
        _USAGE[k] = 0


def _account(messages: List[Dict[str, str]], content: Optional[str], usage: Any = None) -> None:
    """记一次调用用量：有 API 精确 usage 就用，否则按文本估算。"""
    pt = ct = None
    if usage is not None:
        pt = getattr(usage, "prompt_tokens", None)
        ct = getattr(usage, "completion_tokens", None)
        if isinstance(usage, dict):
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if pt is None or ct is None:
        pt = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        ct = estimate_tokens(content or "")
    add_usage(pt, ct)


class LLMConfig(BaseModel):
    """LLM 配置"""

    base_url: str = "https://relay.dinghappy.com/v1"
    api_key: str = ""
    # 默认模型读 .env 的 DEFAULT_MODEL/OPENAI_MODEL（之前写死 gpt-4o-mini，令牌无权会 403）
    model: str = Field(default_factory=lambda: os.getenv("DEFAULT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini")
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: float = 120.0


class LLMClient:
    """LLM 客户端 - OpenAI 兼容接口，带连接池复用"""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()

        # 从环境变量读取配置
        if not self.config.api_key:
            self.config.api_key = os.getenv("OPENAI_API_KEY", "")
        if not self.config.base_url:
            self.config.base_url = os.getenv(
                "OPENAI_API_BASE", "https://relay.dinghappy.com/v1"
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
    ):
        """异步逐块产出文本增量（用于流式展示）"""
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
            content = getattr(delta, "content", None) if delta else None
            if content:
                parts.append(content)
                yield content
        _account(messages, "".join(parts), exact)   # 有精确 usage 用精确，否则估算

    async def _chat_with_requests(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """使用 aiohttp 发送请求（openai 库不可用时的降级方案）"""
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
                else:
                    error = await response.text()
                    raise Exception(
                        f"LLM request failed: {response.status} - {error}"
                    )

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
        base_url=os.getenv("OPENAI_API_BASE", "https://relay.dinghappy.com/v1"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=model_info["model"],
    )
    client = LLMClient(config)
    _client_cache[model_tier] = client
    return client
