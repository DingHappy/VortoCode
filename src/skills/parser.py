"""SKILL.md 文件解析器"""

import yaml
from pathlib import Path
from typing import Dict, List, Tuple

from .skill import SkillArgument, SkillHook, SkillMetadata


class SkillParser:
    """SKILL.md 文件解析器"""
    
    @staticmethod
    def parse_file(file_path: Path) -> Tuple[SkillMetadata, str, List[SkillArgument], Dict[str, List[SkillHook]]]:
        """解析 SKILL.md 文件"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return SkillParser.parse_content(content, file_path.parent.name)
    
    @staticmethod
    def parse_content(content: str, default_name: str = "unknown") -> Tuple[SkillMetadata, str, List[SkillArgument], Dict[str, List[SkillHook]]]:
        """解析技能内容"""
        # 分离 frontmatter 和内容
        parts = content.split("---", 2)
        
        if len(parts) < 3:
            # 没有 frontmatter
            metadata = SkillMetadata(name=default_name)
            return metadata, content.strip(), [], {}
        
        # 解析 YAML frontmatter
        frontmatter = yaml.safe_load(parts[1]) or {}
        
        # 解析元数据
        metadata = SkillMetadata(
            name=frontmatter.get("name", default_name),
            description=frontmatter.get("description", ""),
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
            capabilities=frontmatter.get("capabilities", []),
            tools=frontmatter.get("tools", []),
            skills=frontmatter.get("skills", []),
            context=frontmatter.get("context"),
            agent=frontmatter.get("agent"),
            model=frontmatter.get("model", "inherit"),
            effort=frontmatter.get("effort", "medium"),
            disable_model_invocation=frontmatter.get("disable-model-invocation", False),
            user_invocable=frontmatter.get("user-invocable", True)
        )
        
        # 解析参数
        arguments = []
        for arg_data in frontmatter.get("arguments", []):
            if isinstance(arg_data, dict):
                arguments.append(SkillArgument(
                    name=arg_data.get("name", ""),
                    description=arg_data.get("description", ""),
                    required=arg_data.get("required", True),
                    default=arg_data.get("default")
                ))
            elif isinstance(arg_data, str):
                arguments.append(SkillArgument(name=arg_data))
        
        # 解析 hooks
        hooks = {}
        for event, hook_list in frontmatter.get("hooks", {}).items():
            if isinstance(hook_list, list):
                hooks[event] = [
                    SkillHook(**hook) if isinstance(hook, dict) else SkillHook()
                    for hook in hook_list
                ]
        
        # 获取指令内容
        instructions = parts[2].strip()
        
        return metadata, instructions, arguments, hooks
    
    @staticmethod
    def discover_skills(skill_dirs: List[str]) -> List[Path]:
        """发现技能文件"""
        skill_files = []
        
        for skill_dir in skill_dirs:
            skill_path = Path(skill_dir).expanduser()
            if skill_path.exists():
                # 递归查找 SKILL.md 文件
                for skill_file in skill_path.rglob("SKILL.md"):
                    skill_files.append(skill_file)
        
        return skill_files
