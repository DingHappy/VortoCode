"""开发生命周期技能 - 参考 Agent Skills"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DevelopmentPhase(str, Enum):
    """开发生命周期阶段"""
    DEFINE = "define"      # 定义需求
    PLAN = "plan"          # 规划任务
    BUILD = "build"        # 构建实现
    VERIFY = "verify"      # 验证测试
    REVIEW = "review"      # 代码审查
    SHIP = "ship"          # 发布部署


class SkillTrigger(BaseModel):
    """技能触发条件"""
    commands: List[str] = Field(default_factory=list)  # 斜杠命令
    keywords: List[str] = Field(default_factory=list)  # 关键词
    file_patterns: List[str] = Field(default_factory=list)  # 文件模式
    auto_invoke: bool = False  # 自动调用


class AntiRationalization(BaseModel):
    """反合理化规则"""
    excuse: str  # 常见借口
    rebuttal: str  # 反驳
    severity: str = "medium"  # low, medium, high


class VerificationGate(BaseModel):
    """验证门控"""
    name: str
    description: str
    evidence_required: List[str]  # 需要的证据
    auto_check: bool = True


class LifecycleSkill(BaseModel):
    """生命周期技能"""
    name: str
    phase: DevelopmentPhase
    description: str
    trigger: SkillTrigger
    process: List[str]  # 步骤流程
    anti_rationalizations: List[AntiRationalization] = Field(default_factory=list)
    verification_gates: List[VerificationGate] = Field(default_factory=list)
    red_flags: List[str] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)


# 预定义的生命周期技能
LIFECYCLE_SKILLS: List[LifecycleSkill] = [
    # ==================== DEFINE 阶段 ====================
    LifecycleSkill(
        name="interview-me",
        phase=DevelopmentPhase.DEFINE,
        description="通过结构化访谈提取用户真实需求",
        trigger=SkillTrigger(
            commands=["/interview", "/需求"],
            keywords=["需求", "requirement", "想要", "需要"],
            auto_invoke=False
        ),
        process=[
            "1. 一次只问一个问题，不要批量提问",
            "2. 从开放性问题开始，逐步收窄",
            "3. 确认用户的实际使用场景",
            "4. 识别隐含需求和约束",
            "5. 总结并确认理解",
            "6. 达到 95% 置信度后输出需求文档"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="用户说得很清楚了，不需要再问",
                rebuttal="用户的描述往往是表面需求，深层需求需要挖掘",
                severity="high"
            ),
            AntiRationalization(
                excuse="问太多问题会让用户烦",
                rebuttal="现在问清楚比返工好 100 倍",
                severity="medium"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="需求确认",
                description="用户确认需求理解正确",
                evidence_required=["用户明确确认", "需求文档已生成"]
            )
        ],
        red_flags=[
            "用户频繁改变主意",
            "需求描述模糊不清",
            "没有明确的验收标准"
        ]
    ),
    
    LifecycleSkill(
        name="spec-driven-development",
        phase=DevelopmentPhase.DEFINE,
        description="在编写代码前先写规格说明",
        trigger=SkillTrigger(
            commands=["/spec", "/规格"],
            keywords=["spec", "specification", "PRD", "规格"],
            auto_invoke=False
        ),
        process=[
            "1. 明确项目目标和范围",
            "2. 定义用户故事和验收标准",
            "3. 设计 API 接口契约",
            "4. 确定技术栈和架构",
            "5. 列出约束和边界",
            "6. 生成 SPEC.md 文件"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="项目很简单，不需要写规格",
                rebuttal="越简单越容易忽略细节，规格是质量保证",
                severity="high"
            ),
            AntiRationalization(
                excuse="写规格太花时间",
                rebuttal="规格节省的是返工时间，投入产出比很高",
                severity="medium"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="规格完整性",
                description="规格文档包含所有必要部分",
                evidence_required=["目标", "用户故事", "API 契约", "技术栈", "约束"]
            )
        ]
    ),
    
    # ==================== PLAN 阶段 ====================
    LifecycleSkill(
        name="planning-and-task-breakdown",
        phase=DevelopmentPhase.PLAN,
        description="将规格分解为小的、可验证的任务",
        trigger=SkillTrigger(
            commands=["/plan", "/规划"],
            keywords=["plan", "breakdown", "分解", "规划"],
            auto_invoke=False
        ),
        process=[
            "1. 阅读规格文档",
            "2. 识别主要功能模块",
            "3. 将模块分解为小任务（< 100 行代码）",
            "4. 确定任务依赖关系",
            "5. 估算每个任务的工作量",
            "6. 创建任务列表和时间线"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="直接写代码更快",
                rebuttal="没有计划的代码会走弯路，最终更慢",
                severity="high"
            ),
            AntiRationalization(
                excuse="任务太小不值得单独列",
                rebuttal="小任务更容易验证和回滚",
                severity="medium"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="任务原子性",
                description="每个任务都是原子的、可验证的",
                evidence_required=["任务列表", "依赖图", "验收标准"]
            )
        ]
    ),
    
    # ==================== BUILD 阶段 ====================
    LifecycleSkill(
        name="incremental-implementation",
        phase=DevelopmentPhase.BUILD,
        description="增量实现：一个切片一个切片地构建",
        trigger=SkillTrigger(
            commands=["/build", "/构建"],
            keywords=["implement", "build", "实现", "构建"],
            auto_invoke=False
        ),
        process=[
            "1. 选择下一个待实现的任务",
            "2. 先写测试（TDD）",
            "3. 实现最小可行代码",
            "4. 运行测试验证",
            "5. 重构优化",
            "6. 提交代码",
            "7. 更新任务状态"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="测试可以最后写",
                rebuttal="最后写的测试只能验证现有代码，不能驱动设计",
                severity="high"
            ),
            AntiRationalization(
                excuse="这个功能很简单，不需要测试",
                rebuttal="简单功能也容易出 bug，测试是证明正确性的唯一方式",
                severity="high"
            ),
            AntiRationalization(
                excuse="重构会让代码变复杂",
                rebuttal="重构的目的是让代码更简单，不是更复杂",
                severity="medium"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="测试通过",
                description="所有测试必须通过",
                evidence_required=["测试输出", "覆盖率报告"]
            ),
            VerificationGate(
                name="代码提交",
                description="代码已提交到版本控制",
                evidence_required=["commit hash", "commit message"]
            )
        ],
        red_flags=[
            "测试失败但继续开发",
            "跳过重构步骤",
            "一次提交太多更改"
        ]
    ),
    
    LifecycleSkill(
        name="test-driven-development",
        phase=DevelopmentPhase.BUILD,
        description="测试驱动开发：红-绿-重构",
        trigger=SkillTrigger(
            commands=["/tdd", "/测试驱动"],
            keywords=["test", "TDD", "测试"],
            auto_invoke=False
        ),
        process=[
            "1. 写一个失败的测试（红）",
            "2. 写最少代码让测试通过（绿）",
            "3. 重构代码（重构）",
            "4. 重复以上步骤"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="TDD 太慢了",
                rebuttal="TDD 减少调试时间，总体更快",
                severity="high"
            ),
            AntiRationalization(
                excuse="我不知道该写什么测试",
                rebuttal="如果你不知道该测什么，你也不知道该写什么代码",
                severity="high"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="测试金字塔",
                description="遵循测试金字塔原则",
                evidence_required=["单元测试 > 80%", "集成测试 > 15%", "E2E < 5%"]
            )
        ]
    ),
    
    # ==================== VERIFY 阶段 ====================
    LifecycleSkill(
        name="debugging-and-error-recovery",
        phase=DevelopmentPhase.VERIFY,
        description="五步调试法：复现、定位、缩小、修复、防护",
        trigger=SkillTrigger(
            commands=["/debug", "/调试"],
            keywords=["debug", "error", "bug", "调试", "错误"],
            auto_invoke=False
        ),
        process=[
            "1. 复现问题（最小复现步骤）",
            "2. 定位问题根源",
            "3. 缩小问题范围",
            "4. 修复问题",
            "5. 添加防护测试",
            "6. 验证修复"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="问题很明显，直接修复",
                rebuttal="表面问题可能是深层问题的症状",
                severity="high"
            ),
            AntiRationalization(
                excuse="加个 if 判断就行了",
                rebuttal="条件判断是掩盖问题，不是解决问题",
                severity="high"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="问题复现",
                description="问题可以稳定复现",
                evidence_required=["复现步骤", "错误日志"]
            ),
            VerificationGate(
                name="防护测试",
                description="添加了防止问题再次出现的测试",
                evidence_required=["测试代码"]
            )
        ]
    ),
    
    # ==================== REVIEW 阶段 ====================
    LifecycleSkill(
        name="code-review-and-quality",
        phase=DevelopmentPhase.REVIEW,
        description="五维度代码审查：正确性、可读性、性能、安全、可维护性",
        trigger=SkillTrigger(
            commands=["/review", "/审查"],
            keywords=["review", "审查", "code review"],
            auto_invoke=False
        ),
        process=[
            "1. 检查代码正确性",
            "2. 评估可读性和命名",
            "3. 分析性能影响",
            "4. 检查安全漏洞",
            "5. 评估可维护性",
            "6. 提供改进建议",
            "7. 给出审查结论"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="代码能跑就行",
                rebuttal="能跑的代码不一定是好代码",
                severity="high"
            ),
            AntiRationalization(
                excuse="这个改动很小，不需要审查",
                rebuttal="小改动也能引入大问题",
                severity="medium"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="审查完成",
                description="代码审查已完成",
                evidence_required=["审查报告", "改进建议"]
            )
        ]
    ),
    
    LifecycleSkill(
        name="security-and-hardening",
        phase=DevelopmentPhase.REVIEW,
        description="安全加固：OWASP Top 10 防护",
        trigger=SkillTrigger(
            commands=["/security", "/安全"],
            keywords=["security", "安全", "vulnerability"],
            auto_invoke=False
        ),
        process=[
            "1. 检查输入验证",
            "2. 检查认证和授权",
            "3. 检查敏感数据处理",
            "4. 检查 SQL 注入风险",
            "5. 检查 XSS 风险",
            "6. 检查依赖安全",
            "7. 生成安全报告"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="内部系统不需要太关注安全",
                rebuttal="内部系统也可能被攻击，安全是基本要求",
                severity="high"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="安全检查",
                description="通过安全检查清单",
                evidence_required=["OWASP 检查", "依赖扫描"]
            )
        ]
    ),
    
    # ==================== SHIP 阶段 ====================
    LifecycleSkill(
        name="git-workflow-and-versioning",
        phase=DevelopmentPhase.SHIP,
        description="Git 工作流：原子提交、trunk-based 开发",
        trigger=SkillTrigger(
            commands=["/git", "/提交"],
            keywords=["commit", "push", "git", "提交"],
            auto_invoke=False
        ),
        process=[
            "1. 检查代码状态",
            "2. 暂存相关更改",
            "3. 编写清晰的提交信息",
            "4. 原子提交（一个功能一个提交）",
            "5. 推送到远程仓库"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="提交信息不重要",
                rebuttal="提交信息是给未来的自己和队友看的",
                severity="medium"
            ),
            AntiRationalization(
                excuse="先提交，之后再整理",
                rebuttal="之后永远不会整理，现在就做好",
                severity="medium"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="提交规范",
                description="提交信息符合规范",
                evidence_required=["commit message", "原子性"]
            )
        ]
    ),
    
    LifecycleSkill(
        name="shipping-and-launch",
        phase=DevelopmentPhase.SHIP,
        description="发布检查清单：确保准备就绪",
        trigger=SkillTrigger(
            commands=["/ship", "/发布"],
            keywords=["deploy", "release", "ship", "发布", "部署"],
            auto_invoke=False
        ),
        process=[
            "1. 运行所有测试",
            "2. 检查构建状态",
            "3. 更新版本号",
            "4. 更新文档",
            "5. 创建发布说明",
            "6. 部署到测试环境",
            "7. 验证功能",
            "8. 部署到生产环境",
            "9. 监控发布状态"
        ],
        anti_rationalizations=[
            AntiRationalization(
                excuse="测试环境没问题，生产环境也一样",
                rebuttal="生产环境有更多变量，不能假设一致",
                severity="high"
            ),
            AntiRationalization(
                excuse="先发布，有问题再修复",
                rebuttal="发布后修复成本是发布前的 100 倍",
                severity="high"
            )
        ],
        verification_gates=[
            VerificationGate(
                name="发布检查",
                description="通过发布检查清单",
                evidence_required=["测试通过", "构建成功", "文档更新"]
            )
        ]
    )
]


class LifecycleSkillManager:
    """生命周期技能管理器"""
    
    def __init__(self):
        self.skills: Dict[str, LifecycleSkill] = {}
        self._load_default_skills()
    
    def _load_default_skills(self):
        """加载默认技能"""
        for skill in LIFECYCLE_SKILLS:
            self.skills[skill.name] = skill
    
    def get_skill(self, name: str) -> Optional[LifecycleSkill]:
        """获取技能"""
        return self.skills.get(name)
    
    def get_skills_by_phase(self, phase: DevelopmentPhase) -> List[LifecycleSkill]:
        """按阶段获取技能"""
        return [s for s in self.skills.values() if s.phase == phase]
    
    def find_skill_by_command(self, command: str) -> Optional[LifecycleSkill]:
        """根据命令查找技能"""
        for skill in self.skills.values():
            if command in skill.trigger.commands:
                return skill
        return None
    
    def find_skill_by_keyword(self, keyword: str) -> List[LifecycleSkill]:
        """根据关键词查找技能"""
        results = []
        for skill in self.skills.values():
            if keyword.lower() in [k.lower() for k in skill.trigger.keywords]:
                results.append(skill)
        return results
    
    def get_skill_prompt(self, name: str, context: Dict[str, Any] = None) -> str:
        """获取技能的执行提示"""
        skill = self.skills.get(name)
        if not skill:
            return ""
        
        prompt_parts = [
            f"# 技能: {skill.name}",
            f"阶段: {skill.phase.value}",
            f"描述: {skill.description}",
            "",
            "## 执行步骤",
            *skill.process,
            ""
        ]
        
        # 添加反合理化规则
        if skill.anti_rationalizations:
            prompt_parts.append("## 常见借口和反驳")
            for ar in skill.anti_rationalizations:
                prompt_parts.append(f"- 借口: {ar.excuse}")
                prompt_parts.append(f"  反驳: {ar.rebuttal}")
            prompt_parts.append("")
        
        # 添加验证门控
        if skill.verification_gates:
            prompt_parts.append("## 验证要求")
            for gate in skill.verification_gates:
                prompt_parts.append(f"- {gate.name}: {gate.description}")
                prompt_parts.append(f"  需要证据: {', '.join(gate.evidence_required)}")
            prompt_parts.append("")
        
        # 添加红旗警告
        if skill.red_flags:
            prompt_parts.append("## 红旗警告")
            for flag in skill.red_flags:
                prompt_parts.append(f"- ⚠️ {flag}")
        
        return "\n".join(prompt_parts)
    
    def list_commands(self) -> List[Dict[str, str]]:
        """列出所有命令"""
        commands = []
        for skill in self.skills.values():
            for cmd in skill.trigger.commands:
                commands.append({
                    "command": cmd,
                    "skill": skill.name,
                    "description": skill.description
                })
        return commands
