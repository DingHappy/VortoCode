"""技能系统"""

from .skill import Skill, SkillMetadata, SkillArgument, SkillHook, SkillResult
from .parser import SkillParser
from .registry import SkillRegistry, SimpleSkill
from .executor import SkillExecutor
from .composer import SkillComposer, CompositeSkill, ConditionalSkill
from .skill_manager import (
    SkillDefinition,
    Skill as ManagedSkill,
    SkillRegistry as ManagedSkillRegistry,
    SkillManager
)
from .lifecycle import (
    DevelopmentPhase,
    SkillTrigger,
    AntiRationalization,
    VerificationGate,
    LifecycleSkill,
    LifecycleSkillManager,
    LIFECYCLE_SKILLS
)
from .checklists import (
    ChecklistCategory,
    ChecklistItem,
    Checklist,
    ChecklistManager,
    TESTING_CHECKLIST,
    SECURITY_CHECKLIST,
    PERFORMANCE_CHECKLIST,
    CODE_QUALITY_CHECKLIST
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
    "SkillComposer",
    "CompositeSkill",
    "ConditionalSkill",
    "SkillDefinition",
    "ManagedSkill",
    "ManagedSkillRegistry",
    "SkillManager",
    
    # 生命周期技能
    "DevelopmentPhase",
    "SkillTrigger",
    "AntiRationalization",
    "VerificationGate",
    "LifecycleSkill",
    "LifecycleSkillManager",
    "LIFECYCLE_SKILLS",
    
    # 检查清单
    "ChecklistCategory",
    "ChecklistItem",
    "Checklist",
    "ChecklistManager",
    "TESTING_CHECKLIST",
    "SECURITY_CHECKLIST",
    "PERFORMANCE_CHECKLIST",
    "CODE_QUALITY_CHECKLIST",
]
