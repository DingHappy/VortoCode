"""技能系统"""

from .skill import Skill, SkillMetadata, SkillArgument, SkillHook, SkillResult
from .parser import SkillParser
from .registry import SkillRegistry, SimpleSkill
from .executor import SkillExecutor
from .skill_manager import (
    SkillDefinition,
    Skill as ManagedSkill,
    SkillRegistry as ManagedSkillRegistry,
    SkillManager
)
from .discovery import (
    SkillVersion,
    SkillManifest,
    SkillDiscoveryConfig,
    SkillDiscovery,
    SkillVersionManager,
    AutoReloadingSkillDiscovery,
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
    "SkillDefinition",
    "ManagedSkill",
    "ManagedSkillRegistry",
    "SkillManager",

    # 技能发现
    "SkillVersion",
    "SkillManifest",
    "SkillDiscoveryConfig",
    "SkillDiscovery",
    "SkillVersionManager",
    "AutoReloadingSkillDiscovery",
    "create_skill_discovery",
]
