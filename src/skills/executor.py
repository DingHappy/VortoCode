"""技能执行器"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .skill import Skill, SkillResult
from .registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillExecutor:
    """技能执行器"""
    
    def __init__(
        self, 
        registry: SkillRegistry,
        agent_factory: Any = None  # AgentFactory 类型
    ):
        self.registry = registry
        self.agent_factory = agent_factory
        self.execution_history: List[Dict[str, Any]] = []
    
    async def execute(
        self, 
        skill_name: str,
        args: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> SkillResult:
        """执行技能"""
        # 获取技能
        skill = self.registry.get(skill_name)
        if not skill:
            return SkillResult(
                success=False,
                error=f"Skill not found: {skill_name}"
            )
        
        # 检查是否需要在子代理中执行
        if skill.metadata.context == "fork":
            result = await self._execute_in_subagent(skill, args, context)
        else:
            result = await self._execute_inline(skill, args, context)
        
        # 记录执行历史
        self.execution_history.append({
            "skill_name": skill_name,
            "args": args,
            "success": result.success,
            "error": result.error
        })
        
        return result
    
    async def _execute_inline(
        self, 
        skill: Skill,
        args: Optional[Dict[str, Any]],
        context: Optional[Dict[str, Any]]
    ) -> SkillResult:
        """内联执行技能"""
        return await skill.execute(args, context)
    
    async def _execute_in_subagent(
        self, 
        skill: Skill,
        args: Optional[Dict[str, Any]],
        context: Optional[Dict[str, Any]]
    ) -> SkillResult:
        """在子代理中执行技能"""
        if not self.agent_factory:
            return SkillResult(
                success=False,
                error="AgentFactory not configured for subagent execution"
            )
        
        try:
            # 创建子代理
            agent_type = skill.metadata.agent or "general-purpose"
            agent = self.agent_factory.create_agent(
                agent_type=agent_type,
                tools=skill.metadata.tools,
                model=skill.metadata.model
            )
            
            # 准备任务描述
            task = skill._render_instructions(args or {}, context)
            
            # 执行任务
            result = await agent.execute(task)
            
            return SkillResult(
                success=result.success,
                output=result.output,
                error=result.error,
                artifacts=getattr(result, 'files_modified', []),
                metrics={
                    "tokens_used": getattr(result, 'tokens_used', 0),
                    "duration": getattr(result, 'duration', 0)
                }
            )
        
        except Exception as e:
            logger.error(f"Subagent execution failed: {e}")
            return SkillResult(
                success=False,
                error=str(e)
            )
    
    async def execute_chain(
        self, 
        skill_names: List[str],
        initial_args: Optional[Dict[str, Any]] = None
    ) -> List[SkillResult]:
        """执行技能链"""
        results = []
        current_args = initial_args or {}
        
        for skill_name in skill_names:
            result = await self.execute(skill_name, current_args)
            results.append(result)
            
            if result.success:
                # 将上一个结果传递给下一个技能
                current_args["previous_output"] = result.output
            else:
                # 链中断
                logger.warning(f"Skill chain broken at {skill_name}: {result.error}")
                break
        
        return results
    
    async def execute_parallel(
        self, 
        skill_names: List[str],
        args: Optional[Dict[str, Any]] = None
    ) -> List[SkillResult]:
        """并行执行多个技能"""
        tasks = [
            self.execute(skill_name, args) 
            for skill_name in skill_names
        ]
        
        return await asyncio.gather(*tasks, return_exceptions=False)
