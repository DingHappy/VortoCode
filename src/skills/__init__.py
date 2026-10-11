"""技能系统"""

from .skill import Skill, SkillMetadata, SkillArgument, SkillHook, SkillResult
from .parser import SkillParser
from .registry import SkillRegistry, SimpleSkill
from .executor import SkillExecutor
from .discovery import (
    SkillVersion,
    SkillManifest,
    SkillDiscoveryConfig,
    SkillDiscovery,
    SkillVersionManager,
    create_skill_discovery
)

__all__ = [
    # 基础技能
    "Skill",
    "SkillMetadata",
    "SkillArgument",
    "SkillHook",
    "SkillResult",
    "SkillParser",
    "SkillRegistry",
    "SimpleSkill",
    "SkillExecutor",

    # 技能发现
    "SkillVersion",
    "SkillManifest",
    "SkillDiscoveryConfig",
    "SkillDiscovery",
    "SkillVersionManager",
    "create_skill_discovery",
]
