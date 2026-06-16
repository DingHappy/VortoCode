"""通用 LLM Agent：文本进、文本出。

供 AutonomousLoop 等"通用目标推进"流程使用——这类流程需要一个能直接对任意提示
返回文本的 Agent（用于规划、单步推进、进度自评等），而角色 Agent 都是专用的。
"""

from typing import Any, Optional

from .base import Agent, AgentConfig, AgentResult


class LLMAgent(Agent):
    """最简通用 Agent：把任务原样交给 LLM，返回文本输出。"""

    def __init__(self, config: Optional[AgentConfig] = None, llm_client: Any = None):
        super().__init__(
            config or AgentConfig(role="general", name="LLM Agent", description="通用文本 Agent"),
            llm_client=llm_client,
        )

    async def execute(self, task: str, **kwargs) -> AgentResult:
        try:
            on_token = (self._merge_context(kwargs)).get("on_token")
            text = await self._complete(task, on_token=on_token)
            return AgentResult(success=True, output=text, metadata={"role": "general"})
        except Exception as e:
            return AgentResult(success=False, error=str(e))
