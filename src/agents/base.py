"""Agent 基类和配置"""

import asyncio
import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def extract_json(text: str) -> Optional[Union[dict, list]]:
    """从 LLM 输出中健壮地提取 JSON。

    处理三种常见情况：纯 JSON、被 ```json 围栏包裹、以及 JSON 前后夹杂解释文字。
    解析失败返回 None（由调用方决定降级策略），绝不抛异常打断流程。
    """
    if not text:
        return None
    t = text.strip()

    # 去除 markdown 代码围栏
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t).strip()

    # 先尝试整体解析
    try:
        return json.loads(t)
    except Exception:
        pass

    # 回退：扫描第一个配平的 {...} 或 [...]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = t.find(open_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
    return None


class AgentStatus(str, Enum):
    """Agent 状态"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


class AgentConfig(BaseModel):
    """Agent 配置"""
    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    role: str = "general"
    name: str = ""
    description: str = ""
    model: str = "inherit"
    system_prompt: str = ""
    tools: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    max_iterations: int = 10
    timeout: int = 300  # 秒
    temperature: float = 0.7
    verbose: bool = False


class AgentResult(BaseModel):
    """Agent 执行结果"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    # 本次执行的推理过程（推理型模型的思维链；普通模型为 None）。用于可审计与自我分析。
    reasoning: Optional[str] = None
    files_created: List[str] = Field(default_factory=list)
    files_modified: List[str] = Field(default_factory=list)
    tokens_used: int = 0
    duration: float = 0.0
    iterations: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentCapability(BaseModel):
    """Agent 能力声明"""
    name: str
    description: str = ""
    tools: List[str] = Field(default_factory=list)
    confidence: float = 0.8  # 0-1


class Agent(ABC):
    """Agent 基类"""
    
    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        llm_client: Optional[Any] = None,
    ):
        self.config = config or AgentConfig()
        self.status = AgentStatus.IDLE
        self.capabilities: List[AgentCapability] = []
        self.tools: Dict[str, Any] = {}
        self.context: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []
        self._start_time: Optional[datetime] = None
        # LLM 客户端可注入（便于离线测试），否则首次使用时懒加载
        self._llm_client = llm_client
        # 最近一次 _complete 调用捕获到的推理内容（思维链），由各角色回填进 AgentResult
        self._last_reasoning: Optional[str] = None

    @property
    def agent_id(self) -> str:
        return self.config.agent_id

    @property
    def role(self) -> str:
        return self.config.role

    @property
    def llm(self) -> Any:
        """LLM 客户端（懒加载，可在构造时注入以便测试）"""
        if self._llm_client is None:
            from ..llm import get_llm_client
            self._llm_client = get_llm_client("balanced")
        return self._llm_client

    async def _complete(
        self,
        prompt: str,
        system: str = "",
        temperature: Optional[float] = None,
        on_token: Optional[Any] = None,
    ) -> str:
        """调用 LLM 完成一次对话，返回完整文本。

        若提供 on_token 且客户端支持流式，则逐块回调 on_token（用于实时展示），
        同时累积返回完整文本；流式失败或不支持时自动退回一次性返回。
        """
        sys_prompt = system or self.config.system_prompt
        messages: List[Dict[str, str]] = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        messages.append({"role": "user", "content": prompt})

        model = None if self.config.model in ("", "inherit") else self.config.model
        temp = temperature if temperature is not None else self.config.temperature

        # 每次调用先清空，避免上一轮的推理串味到本轮结果
        self._last_reasoning = None

        from ..core.monitoring import metrics
        import time as _t
        _start = _t.time()
        _ok = True
        try:
            if on_token is not None and hasattr(self.llm, "stream"):
                try:
                    parts: List[str] = []
                    async for tok in self.llm.stream(messages, model=model, temperature=temp):
                        if not tok:
                            continue
                        parts.append(tok)
                        res = on_token(tok)
                        if asyncio.iscoroutine(res):
                            await res
                    return "".join(parts)
                except Exception as e:
                    logger.warning("[%s] 流式失败，退回一次性返回: %s", self.role, e)

            resp = await self.llm.chat(messages, model=model, temperature=temp)
            if isinstance(resp, dict):
                reasoning = resp.get("reasoning")
                if reasoning:
                    self._last_reasoning = reasoning
                    logger.debug("[%s] 捕获推理链 %d 字", self.role, len(reasoning))
                usage = resp.get("usage") or {}
                if usage.get("total_tokens"):
                    metrics.increment("llm.tokens", int(usage["total_tokens"]))
                    try:
                        from ..models import track_usage
                        track_usage(
                            resp.get("model") or self.config.model,
                            int(usage.get("prompt_tokens", 0)),
                            int(usage.get("completion_tokens", 0)),
                            agent=self.role,
                        )
                    except Exception:
                        pass
                return resp.get("content", "") or ""
            return str(resp)
        except Exception:
            _ok = False
            raise
        finally:
            metrics.increment("llm.calls", labels={"role": self.role})
            if not _ok:
                metrics.increment("llm.errors", labels={"role": self.role})
            metrics.record_time("llm.latency_s", _t.time() - _start, labels={"role": self.role})

    async def _complete_json(
        self,
        prompt: str,
        system: str = "",
        default: Optional[Any] = None,
        on_token: Optional[Any] = None,
    ) -> Any:
        """调用 LLM 并解析为 JSON；解析失败返回 default。"""
        raw = await self._complete(prompt, system=system, on_token=on_token)
        data = extract_json(raw)
        if data is None:
            logger.warning(
                "[%s] LLM 未返回可解析的 JSON，使用降级值。原始输出前 200 字: %s",
                self.role, (raw or "")[:200],
            )
            return default
        return data

    def _resolve_workspace(self, context: Dict[str, Any]) -> Path:
        """确定工作区目录：优先 context，其次默认到 .vortocode/workspaces/<id>/。"""
        ws = context.get("workspace")
        if not ws:
            ws = Path(".vortocode") / "workspaces" / self.agent_id
        ws = Path(ws)
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    @staticmethod
    def _merge_context(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """合并 execute 收到的 context 字典与直接 kwargs（kwargs 优先）。"""
        ctx = dict(kwargs.get("context") or {})
        for k, v in kwargs.items():
            if k != "context":
                ctx[k] = v
        return ctx
    
    @abstractmethod
    async def execute(self, task: str, **kwargs) -> AgentResult:
        """执行任务"""
        pass
    
    async def initialize(self) -> None:
        """初始化 Agent"""
        self.status = AgentStatus.IDLE
        await self._load_tools()
        await self._load_capabilities()
    
    async def _load_tools(self) -> None:
        """加载工具"""
        # 子类可以覆盖此方法来加载特定工具
        pass
    
    async def _load_capabilities(self) -> None:
        """加载能力"""
        # 子类可以覆盖此方法来声明能力
        pass
    
    def declare_capability(self, capability: AgentCapability) -> None:
        """声明能力"""
        self.capabilities.append(capability)
    
    def has_capability(self, capability_name: str) -> bool:
        """检查是否有某项能力"""
        return any(c.name == capability_name for c in self.capabilities)
    
    def add_tool(self, tool_name: str, tool: Any) -> None:
        """添加工具"""
        self.tools[tool_name] = tool
    
    def get_tool(self, tool_name: str) -> Optional[Any]:
        """获取工具"""
        return self.tools.get(tool_name)
    
    def set_context(self, key: str, value: Any) -> None:
        """设置上下文"""
        self.context[key] = value
    
    def get_context(self, key: str, default: Any = None) -> Any:
        """获取上下文"""
        return self.context.get(key, default)
    
    def add_to_history(self, entry: Dict[str, Any]) -> None:
        """添加到历史记录"""
        entry["timestamp"] = datetime.now().isoformat()
        self.history.append(entry)
    
    def clear_history(self) -> None:
        """清除历史记录"""
        self.history.clear()
    
    async def start(self) -> None:
        """启动 Agent"""
        self.status = AgentStatus.RUNNING
        self._start_time = datetime.now()
    
    async def stop(self) -> None:
        """停止 Agent"""
        self.status = AgentStatus.COMPLETED
    
    async def pause(self) -> None:
        """暂停 Agent"""
        self.status = AgentStatus.PAUSED
    
    async def resume(self) -> None:
        """恢复 Agent"""
        self.status = AgentStatus.RUNNING
    
    def get_duration(self) -> float:
        """获取运行时长"""
        if self._start_time:
            return (datetime.now() - self._start_time).total_seconds()
        return 0.0
    
    def __repr__(self) -> str:
        return f"<Agent(id={self.agent_id}, role={self.role}, status={self.status})>"
