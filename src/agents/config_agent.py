"""配置驱动的可执行 Agent。

让"创建的子 agent"不再是死配置：拿它的 system_prompt + model 直接驱动 LLM 执行任务，
复用 base.Agent 的调用与推理链/流式机制（execute 支持 on_token）。
"""

from typing import Any, Optional

from .base import Agent, AgentConfig, AgentResult


class ConfigAgent(Agent):
    """用一段 system_prompt 执行任务的通用 agent。"""

    async def execute(self, task: str, **kwargs) -> AgentResult:
        ctx = self._merge_context(kwargs)
        try:
            output = await self._complete(
                task, system=self.config.system_prompt, on_token=ctx.get("on_token")
            )
            return AgentResult(
                success=bool(output and output.strip()),
                output=output,
                reasoning=self._last_reasoning,
                metadata={"role": self.config.role, "name": self.config.name},
            )
        except Exception as e:  # noqa: BLE001
            return AgentResult(success=False, error=str(e))


def build_config_agent(name: str, role: str = "custom", system_prompt: str = "",
                       model: str = "inherit", llm_client: Optional[Any] = None) -> ConfigAgent:
    """从存储的 agent 字段构造一个可执行 ConfigAgent。"""
    cfg = AgentConfig(
        role=role or "custom",
        name=name,
        system_prompt=system_prompt or f"你是“{name}”，请认真完成用户的任务。",
        model=model or "inherit",
    )
    return ConfigAgent(cfg, llm_client=llm_client)
