"""技能注册表"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from .skill import Skill, SkillMetadata, SkillResult
from .parser import SkillParser

logger = logging.getLogger(__name__)


class SimpleSkill(Skill):
    """简单技能实现"""
    
    async def execute(
        self, 
        args: Optional[dict] = None,
        context: Optional[dict] = None
    ) -> SkillResult:
        """执行技能"""
        try:
            # 验证参数
            validated_args = self._validate_args(args or {})
            
            # 执行 pre hooks
            await self._execute_hooks("pre_execute", validated_args)
            
            # 渲染指令
            rendered_instructions = self._render_instructions(validated_args, context)
            
            # 返回渲染后的指令作为输出
            return SkillResult(
                success=True,
                output=rendered_instructions,
                metrics={"skill_name": self.metadata.name}
            )
        
        except Exception as e:
            return SkillResult(
                success=False,
                error=str(e)
            )


class SkillRegistry:
    """技能注册表"""
    
    def __init__(self, skill_dirs: Optional[List[str]] = None):
        self.skill_dirs = skill_dirs or [
            ".vortocode/skills",
            "~/.vortocode/skills"
        ]
        self.skills: Dict[str, Skill] = {}
        
        # 自动发现技能
        self._discover_skills()
    
    def _discover_skills(self) -> None:
        """发现并加载技能"""
        skill_files = SkillParser.discover_skills(self.skill_dirs)
        
        for skill_file in skill_files:
            try:
                skill = self._load_skill_file(skill_file)
                self.register(skill)
                logger.info(f"Loaded skill: {skill.metadata.name}")
            except Exception as e:
                logger.error(f"Failed to load skill {skill_file}: {e}")
    
    def _load_skill_file(self, file_path: Path) -> Skill:
        """加载技能文件"""
        metadata, instructions, arguments, hooks = SkillParser.parse_file(file_path)
        
        return SimpleSkill(
            metadata=metadata,
            instructions=instructions,
            arguments=arguments,
            hooks=hooks
        )
    
    def register(self, skill: Skill) -> None:
        """注册技能"""
        self.skills[skill.metadata.name] = skill
    
    def unregister(self, skill_name: str) -> None:
        """注销技能"""
        if skill_name in self.skills:
            del self.skills[skill_name]
    
    def get(self, skill_name: str) -> Optional[Skill]:
        """获取技能"""
        return self.skills.get(skill_name)
    
    def list_skills(
        self, 
        capability: Optional[str] = None,
        user_invocable_only: bool = False
    ) -> List[Skill]:
        """列出技能"""
        skills = list(self.skills.values())
        
        if capability:
            skills = [
                s for s in skills 
                if capability in s.metadata.capabilities
            ]
        
        if user_invocable_only:
            skills = [
                s for s in skills 
                if s.metadata.user_invocable
            ]
        
        return skills
    
    def discover_for_task(
        self, 
        task_requirements: List[str]
    ) -> List[Skill]:
        """为任务发现相关技能"""
        matching_skills = []
        
        for skill in self.skills.values():
            score = self._calculate_match_score(skill, task_requirements)
            if score > 0.3:  # 匹配阈值
                matching_skills.append((score, skill))
        
        # 按匹配分数排序
        matching_skills.sort(key=lambda x: x[0], reverse=True)
        
        return [skill for _, skill in matching_skills]
    
    def _calculate_match_score(
        self, 
        skill: Skill, 
        requirements: List[str]
    ) -> float:
        """计算匹配分数"""
        if not requirements:
            return 0.0
        
        skill_text = f"{skill.metadata.description} {' '.join(skill.metadata.capabilities)}"
        skill_words = set(skill_text.lower().split())
        
        matched = 0
        for req in requirements:
            req_words = set(req.lower().split())
            if req_words & skill_words:
                matched += 1
        
        return matched / len(requirements)
    
    def reload(self) -> None:
        """重新加载所有技能"""
        self.skills.clear()
        self._discover_skills()
    
    def add_skill_directory(self, skill_dir: str) -> None:
        """添加技能目录"""
        if skill_dir not in self.skill_dirs:
            self.skill_dirs.append(skill_dir)
            # 加载新目录中的技能
            skill_files = SkillParser.discover_skills([skill_dir])
            for skill_file in skill_files:
                try:
                    skill = self._load_skill_file(skill_file)
                    self.register(skill)
                except Exception as e:
                    logger.error(f"Failed to load skill {skill_file}: {e}")
