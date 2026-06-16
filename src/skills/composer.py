"""技能组合器"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .skill import Skill, SkillMetadata, SkillResult
from .registry import SkillRegistry

logger = logging.getLogger(__name__)


class CompositeSkill(Skill):
    """组合技能"""
    
    def __init__(
        self, 
        skills: List[Skill],
        composition_type: str = "sequential"
    ):
        self.skills = skills
        self.composition_type = composition_type
        
        # 合并元数据
        metadata = SkillMetadata(
            name=f"composite_{'_'.join(s.metadata.name for s in skills)}",
            description=f"Composite skill: {', '.join(s.metadata.description for s in skills)}",
            capabilities=list(set(
                cap for s in skills for cap in s.metadata.capabilities
            )),
            tools=list(set(
                tool for s in skills for tool in s.metadata.tools
            ))
        )
        
        super().__init__(
            metadata=metadata,
            instructions=""
        )
    
    async def execute(
        self, 
        args: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> SkillResult:
        """执行组合技能"""
        if self.composition_type == "sequential":
            return await self._execute_sequential(args, context)
        elif self.composition_type == "parallel":
            return await self._execute_parallel(args, context)
        else:
            return SkillResult(
                success=False,
                error=f"Unsupported composition type: {self.composition_type}"
            )
    
    async def _execute_sequential(
        self, 
        args: Optional[Dict[str, Any]],
        context: Optional[Dict[str, Any]]
    ) -> SkillResult:
        """顺序执行"""
        results = []
        current_args = args or {}
        
        for skill in self.skills:
            result = await skill.execute(current_args, context)
            results.append(result)
            
            if not result.success:
                return SkillResult(
                    success=False,
                    output=results,
                    error=f"Skill {skill.metadata.name} failed: {result.error}"
                )
            
            # 传递结果给下一个技能
            current_args["previous_output"] = result.output
        
        return SkillResult(
            success=True,
            output=results,
            metrics={"skills_executed": len(results)}
        )
    
    async def _execute_parallel(
        self, 
        args: Optional[Dict[str, Any]],
        context: Optional[Dict[str, Any]]
    ) -> SkillResult:
        """并行执行"""
        tasks = [
            skill.execute(args, context) 
            for skill in self.skills
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 检查是否有异常
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            return SkillResult(
                success=False,
                output=results,
                error=f"Parallel execution failed: {errors}"
            )
        
        return SkillResult(
            success=True,
            output=results,
            metrics={"skills_executed": len(results)}
        )


class SkillComposer:
    """技能组合器"""
    
    def __init__(self, registry: SkillRegistry):
        self.registry = registry
    
    def compose(
        self, 
        skill_names: List[str],
        composition_type: str = "sequential"
    ) -> CompositeSkill:
        """组合多个技能"""
        skills = []
        for name in skill_names:
            skill = self.registry.get(name)
            if not skill:
                raise ValueError(f"Skill not found: {name}")
            skills.append(skill)
        
        return CompositeSkill(
            skills=skills,
            composition_type=composition_type
        )
    
    def compose_conditional(
        self,
        conditions: List[Dict[str, Any]]
    ) -> 'ConditionalSkill':
        """创建条件组合技能"""
        return ConditionalSkill(conditions, self.registry)


class ConditionalSkill(Skill):
    """条件技能"""
    
    def __init__(
        self, 
        conditions: List[Dict[str, Any]],
        registry: SkillRegistry
    ):
        self.conditions = conditions
        self.registry = registry
        
        metadata = SkillMetadata(
            name="conditional_skill",
            description="Conditional skill execution"
        )
        
        super().__init__(metadata=metadata)
    
    async def execute(
        self, 
        args: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> SkillResult:
        """执行条件技能"""
        for condition in self.conditions:
            # 评估条件
            if self._evaluate_condition(condition, args, context):
                skill_name = condition.get("skill")
                if skill_name:
                    skill = self.registry.get(skill_name)
                    if skill:
                        return await skill.execute(args, context)
        
        return SkillResult(
            success=False,
            error="No condition matched"
        )
    
    def _evaluate_condition(
        self, 
        condition: Dict[str, Any],
        args: Optional[Dict[str, Any]],
        context: Optional[Dict[str, Any]]
    ) -> bool:
        """评估条件"""
        # 简单的条件评估
        check = condition.get("check", {})
        
        if not check:
            return True
        
        # 检查参数条件
        if "arg" in check and args:
            arg_name = check["arg"]
            expected = check.get("equals")
            if expected and args.get(arg_name) != expected:
                return False
        
        # 检查上下文条件
        if "context" in check and context:
            ctx_name = check["context"]
            expected = check.get("equals")
            if expected and context.get(ctx_name) != expected:
                return False
        
        return True
