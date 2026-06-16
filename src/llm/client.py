import logging
"""LLM 客户端 - 集成 One API 网关"""

import os
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    # 查找 .env 文件
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass


class LLMConfig(BaseModel):
    """LLM 配置"""
    base_url: str = "https://relay.dinghappy.com/v1"
    api_key: str = ""
    model: str = "mimo-v2.5"
    temperature: float = 0.7
    max_tokens: int = 4096


class LLMClient:
    """LLM 客户端 - OpenAI 兼容接口"""
    
    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()
        
        # 从环境变量读取配置
        if not self.config.api_key:
            self.config.api_key = os.getenv("OPENAI_API_KEY", "")
        if not self.config.base_url:
            self.config.base_url = os.getenv("OPENAI_API_BASE", "https://relay.dinghappy.com/v1")
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False
    ) -> Dict[str, Any]:
        """发送聊天请求（异步，不阻塞事件循环）"""
        try:
            from openai import AsyncOpenAI
        except ImportError:
            # 未安装 openai 库时回退到 aiohttp
            return await self._chat_with_requests(messages, model, temperature, max_tokens)

        client = AsyncOpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
        )

        response = await client.chat.completions.create(
            model=model or self.config.model,
            messages=messages,
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens,
            stream=stream,
        )

        if stream:
            return {"stream": response}

        return {
            "content": response.choices[0].message.content,
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
        """异步逐块产出文本增量（用于流式展示）。无 openai 库时整体产出一次。"""
        try:
            from openai import AsyncOpenAI
        except ImportError:
            result = await self._chat_with_requests(messages, model, temperature, None)
            content = result.get("content", "")
            if content:
                yield content
            return

        client = AsyncOpenAI(base_url=self.config.base_url, api_key=self.config.api_key)
        resp = await client.chat.completions.create(
            model=model or self.config.model,
            messages=messages,
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=self.config.max_tokens,
            stream=True,
        )
        async for chunk in resp:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta else None
            if content:
                yield content

    async def _chat_with_requests(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """使用 requests 发送请求"""
        import aiohttp
        
        url = f"{self.config.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}"
        }
        payload = {
            "model": model or self.config.model,
            "messages": messages,
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens
        }
        
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        "content": data["choices"][0]["message"]["content"],
                        "model": data.get("model", ""),
                        "usage": data.get("usage", {})
                    }
                else:
                    error = await response.text()
                    raise Exception(f"LLM request failed: {response.status} - {error}")
    
    async def analyze(self, prompt: str, system_prompt: str = "") -> str:
        """分析任务"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        result = await self.chat(messages)
        return result.get("content", "")


# 模型配置
MODELS = {
    # 低成本模型（简单任务）
    "cheap": {
        "model": "mimo-v2.5",
        "description": "低成本模型，适合简单任务"
    },
    # 平衡模型（中等任务）
    "balanced": {
        "model": "mimo-v2.5",
        "description": "平衡模型，适合大多数任务"
    },
    # 高性能模型（复杂任务）
    "powerful": {
        "model": "mimo-v2.5",
        "description": "高性能模型，适合复杂任务"
    }
}


def get_llm_client(model_tier: str = "balanced") -> LLMClient:
    """获取 LLM 客户端"""
    config = LLMConfig(
        base_url=os.getenv("OPENAI_API_BASE", "https://relay.dinghappy.com/v1"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=MODELS.get(model_tier, MODELS["balanced"])["model"]
    )
    return LLMClient(config)
