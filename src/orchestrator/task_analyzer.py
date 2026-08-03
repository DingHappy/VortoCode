"""任务分析器"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _resolve_llm(use_llm, llm_client):
    """统一的 LLM 启用判定。

    优先级：显式注入 client > 显式 use_llm 开关 > 自动检测（有 api_key 才启用）。
    导入 get_llm_client 会触发 .env 加载，因此能正确读到 OPENAI_API_KEY。
    返回 (use_llm: bool, client)。
    """
    if llm_client is not None:
        return (use_llm if use_llm is not None else True), llm_client
    if use_llm is False:
        return False, None
    try:
        from ..llm import get_llm_client
        client = get_llm_client("balanced")
    except Exception as e:  # 没装 LLM 依赖等
        logger.warning(f"LLM client unavailable, using rule-based: {e}")
        return False, None
    if use_llm is True:
        return True, client  # 显式要求；无 key 时调用会失败并降级
    # 自动检测：有 key 才启用
    if client.config.api_key:
        return True, client
    return False, None


class TaskComplexity(str, Enum):
    """任务复杂度"""
    TRIVIAL = "trivial"      # 单步完成
    SIMPLE = "simple"        # 2-3 步完成
    MEDIUM = "medium"        # 需要多步协作
    COMPLEX = "complex"      # 需要多 Agent 协作
    EPIC = "epic"            # 需要分解为多个子任务


class TaskAnalysis(BaseModel):
    """任务分析结果"""
    complexity: TaskComplexity
    estimated_effort: str = "medium"  # low, medium, high
    required_capabilities: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    parallelizable: bool = False
    risk_level: str = "medium"  # low, medium, high
    estimated_duration: int = 60  # 秒
    suggested_agents: List[str] = Field(default_factory=list)


class SubTask(BaseModel):
    """子任务"""
    id: str
    title: str
    description: str = ""
    required_capabilities: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)  # 依赖的子任务 ID
    estimated_effort: str = "medium"
    acceptance_criteria: List[str] = Field(default_factory=list)


class TaskAnalyzer:
    """任务分析器"""
    
    def __init__(self, use_llm: Optional[bool] = None, llm_client: Any = None):
        # 默认按是否配置 api_key 自动启用 LLM 增强，否则用规则版（离线/无 key 也能跑）
        self.use_llm, self.llm_client = _resolve_llm(use_llm, llm_client)

        self.complexity_keywords = {
            TaskComplexity.TRIVIAL: ["fix typo", "update readme", "change color"],
            TaskComplexity.SIMPLE: ["add button", "create file", "update config"],
            TaskComplexity.MEDIUM: ["implement feature", "add endpoint", "create module"],
            TaskComplexity.COMPLEX: ["refactor", "migrate", "redesign"],
            TaskComplexity.EPIC: ["build system", "create platform", "full stack"]
        }
    
    async def analyze(self, task: str, context: Optional[Dict[str, Any]] = None) -> TaskAnalysis:
        """分析任务"""
        # 如果启用 LLM，使用 LLM 增强分析
        if self.use_llm and self.llm_client:
            try:
                return await self._analyze_with_llm(task, context)
            except Exception as e:
                logger.warning(f"LLM analysis failed, falling back to rule-based: {e}")
        
        # 基于规则的分析
        return await self._analyze_with_rules(task, context)

    async def _analyze_with_rules(self, task: str, context: Optional[Dict[str, Any]] = None) -> TaskAnalysis:
        """基于规则分析任务"""
        # 评估复杂度
        complexity = self._assess_complexity(task)
        
        # 识别所需能力
        capabilities = self._extract_capabilities(task)
        
        # 估算工作量
        effort = self._estimate_effort(complexity)
        
        # 检测是否可并行
        parallelizable = self._check_parallelizability(task)
        
        # 评估风险
        risk_level = self._assess_risk(task, complexity)
        
        # 估算时长
        duration = self._estimate_duration(complexity)
        
        # 建议 Agent
        suggested_agents = self._suggest_agents(capabilities, complexity)
        
        return TaskAnalysis(
            complexity=complexity,
            estimated_effort=effort,
            required_capabilities=capabilities,
            parallelizable=parallelizable,
            risk_level=risk_level,
            estimated_duration=duration,
            suggested_agents=suggested_agents
        )
    
    async def _analyze_with_llm(self, task: str, context: Optional[Dict[str, Any]] = None) -> TaskAnalysis:
        """使用 LLM 增强分析任务"""
        prompt = f"""分析以下软件开发任务，返回 JSON 格式的分析结果：

任务：{task}

请分析：
1. 复杂度（trivial/simple/medium/complex/epic）
2. 所需能力列表
3. 是否可并行执行
4. 风险等级（low/medium/high）
5. 预估工作量（low/medium/high）
6. 建议的 Agent 角色

返回格式：
{{
    "complexity": "medium",
    "capabilities": ["code_generation", "testing"],
    "parallelizable": false,
    "risk_level": "low",
    "effort": "medium",
    "agents": ["developer"]
}}"""

        system_prompt = """你是一个软件开发任务分析专家。
请根据任务描述分析任务特性，返回 JSON 格式的分析结果。
只返回 JSON，不要有其他内容。"""

        try:
            from ..agents.base import extract_json
            response = await self.llm_client.analyze(prompt, system_prompt)
            result = extract_json(response)
            if not isinstance(result, dict):
                raise ValueError("LLM 未返回 JSON 对象")

            # 映射复杂度
            complexity_map = {
                "trivial": TaskComplexity.TRIVIAL,
                "simple": TaskComplexity.SIMPLE,
                "medium": TaskComplexity.MEDIUM,
                "complex": TaskComplexity.COMPLEX,
                "epic": TaskComplexity.EPIC
            }

            complexity = complexity_map.get(
                str(result.get("complexity", "medium")).lower(), TaskComplexity.MEDIUM
            )
            # 缺字段时用规则推断兜底，避免空能力导致无法分解
            capabilities = result.get("capabilities") or self._extract_capabilities(task)

            return TaskAnalysis(
                complexity=complexity,
                estimated_effort=result.get("effort", "medium"),
                required_capabilities=capabilities,
                parallelizable=bool(result.get("parallelizable", False)),
                risk_level=result.get("risk_level", "medium"),
                estimated_duration=self._estimate_duration(complexity),
                suggested_agents=result.get("agents") or self._suggest_agents(capabilities, complexity),
            )

        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            raise
    
    def _assess_complexity(self, task: str) -> TaskComplexity:
        """评估任务复杂度"""
        task_lower = task.lower()
        
        # 基于关键词评估
        for complexity, keywords in self.complexity_keywords.items():
            for keyword in keywords:
                if keyword in task_lower:
                    return complexity
        
        # 基于长度评估
        word_count = len(task.split())
        if word_count <= 5:
            return TaskComplexity.TRIVIAL
        elif word_count <= 15:
            return TaskComplexity.SIMPLE
        elif word_count <= 30:
            return TaskComplexity.MEDIUM
        else:
            return TaskComplexity.COMPLEX
    
    def _extract_capabilities(self, task: str) -> List[str]:
        """提取所需能力"""
        capabilities = []
        task_lower = task.lower()
        
        capability_keywords = {
            "code_generation": ["implement", "create", "build", "write"],
            "code_review": ["review", "check", "audit"],
            "testing": ["test", "verify", "validate"],
            "architecture": ["design", "architect", "plan"],
            "debugging": ["fix", "debug", "troubleshoot"],
            "documentation": ["document", "readme", "guide"],
            "api_design": ["api", "endpoint", "rest"],
            "database": ["database", "db", "sql", "query"],
            "frontend": ["ui", "frontend", "react", "vue"],
            "backend": ["backend", "server", "api"],
            "devops": ["deploy", "docker", "ci/cd"]
        }
        
        for capability, keywords in capability_keywords.items():
            if any(kw in task_lower for kw in keywords):
                capabilities.append(capability)
        
        return capabilities if capabilities else ["general"]
    
    def _estimate_effort(self, complexity: TaskComplexity) -> str:
        """估算工作量"""
        effort_map = {
            TaskComplexity.TRIVIAL: "low",
            TaskComplexity.SIMPLE: "low",
            TaskComplexity.MEDIUM: "medium",
            TaskComplexity.COMPLEX: "high",
            TaskComplexity.EPIC: "high"
        }
        return effort_map.get(complexity, "medium")
    
    def _check_parallelizability(self, task: str) -> bool:
        """检查是否可并行"""
        # 简单的启发式判断
        parallel_indicators = ["multiple", "several", "various", "different", "and"]
        return any(indicator in task.lower() for indicator in parallel_indicators)
    
    def _assess_risk(self, task: str, complexity: TaskComplexity) -> str:
        """评估风险"""
        risk_indicators = ["production", "critical", "security", "payment", "auth"]
        has_risk_keyword = any(risk in task.lower() for risk in risk_indicators)
        
        if has_risk_keyword or complexity == TaskComplexity.EPIC:
            return "high"
        elif complexity == TaskComplexity.COMPLEX:
            return "medium"
        else:
            return "low"
    
    def _estimate_duration(self, complexity: TaskComplexity) -> int:
        """估算时长（秒）"""
        duration_map = {
            TaskComplexity.TRIVIAL: 60,
            TaskComplexity.SIMPLE: 300,
            TaskComplexity.MEDIUM: 900,
            TaskComplexity.COMPLEX: 1800,
            TaskComplexity.EPIC: 3600
        }
        return duration_map.get(complexity, 600)
    
    def _suggest_agents(
        self, 
        capabilities: List[str], 
        complexity: TaskComplexity
    ) -> List[str]:
        """建议 Agent"""
        agents = []
        
        if "architecture" in capabilities or complexity in [TaskComplexity.COMPLEX, TaskComplexity.EPIC]:
            agents.append("architect")
        
        if "code_generation" in capabilities:
            agents.append("developer")
        
        if "code_review" in capabilities:
            agents.append("reviewer")
        
        if "testing" in capabilities:
            agents.append("tester")
        
        if not agents:
            agents.append("developer")
        
        return agents


class TaskDecomposer:
    """任务分解器"""

    def __init__(self, use_llm: Optional[bool] = None, llm_client: Any = None):
        self.use_llm, self.llm_client = _resolve_llm(use_llm, llm_client)
        self.task_counter = 0

    async def decompose(
        self,
        task: str,
        analysis: TaskAnalysis
    ) -> List[SubTask]:
        """分解任务：复杂任务优先用 LLM，失败/无 LLM 时降级到规则版。"""
        self.task_counter = 0

        # 简单任务无需分解
        if analysis.complexity in [TaskComplexity.TRIVIAL, TaskComplexity.SIMPLE]:
            return [self._create_subtask(
                task, task, required_capabilities=analysis.required_capabilities
            )]

        if self.use_llm and self.llm_client:
            try:
                subs = await self._decompose_with_llm(task, analysis)
                if subs:
                    return subs
            except Exception as e:
                logger.warning(f"LLM decomposition failed, falling back to rules: {e}")

        return self._decompose_with_rules(task, analysis)

    def _decompose_with_rules(self, task: str, analysis: TaskAnalysis) -> List[SubTask]:
        """基于能力的规则分解（链式依赖）。"""
        subtasks: List[SubTask] = []

        if "architecture" in analysis.required_capabilities:
            subtasks.append(self._create_subtask(
                "Design architecture",
                f"Design the architecture for: {task}",
                required_capabilities=["architecture"]
            ))
        if "code_generation" in analysis.required_capabilities:
            subtasks.append(self._create_subtask(
                "Implement code",
                f"Implement the code for: {task}",
                required_capabilities=["code_generation"],
                dependencies=([subtasks[-1].id] if subtasks else [])
            ))
        if "testing" in analysis.required_capabilities:
            subtasks.append(self._create_subtask(
                "Write tests",
                f"Write tests for: {task}",
                required_capabilities=["testing"],
                dependencies=([subtasks[-1].id] if subtasks else [])
            ))
        if "code_review" in analysis.required_capabilities:
            subtasks.append(self._create_subtask(
                "Review code",
                f"Review the code for: {task}",
                required_capabilities=["code_review"],
                dependencies=([subtasks[-1].id] if subtasks else [])
            ))

        if not subtasks:
            subtasks.append(self._create_subtask(task, task))

        return subtasks

    async def _decompose_with_llm(self, task: str, analysis: TaskAnalysis) -> List[SubTask]:
        """用 LLM 拆分为带依赖的子任务；id 规范化、依赖只指向更早子任务（保证 DAG 无环）。"""
        from ..agents.base import extract_json

        prompt = f"""把下面的软件开发任务分解为有序的子任务，返回 JSON 数组。

任务：{task}
复杂度：{analysis.complexity.value}
建议能力：{analysis.required_capabilities}

每个子任务字段：
- id: 短字符串（如 "t1"）
- title: 简短标题
- description: 具体做什么
- required_capabilities: 从 [architecture, code_generation, testing, code_review, documentation, devops] 中选
- dependencies: 依赖的子任务 id 列表（只能引用更靠前的子任务）
- acceptance_criteria: 验收标准列表

**每个子任务都必须产生真实的代码改动。** 不要规划"运行测试""集成验证""最终确认""检查是否
生效"这类**不改文件**的子任务——执行流水线在所有子任务完成后会自动跑一遍集成验证，
你再规划一个就是重复劳动；而且它没有 diff，会被判为失败（真机 2026-08-03：一个
"集成验证与最终确认"子任务烧掉 20 万 token，最后标成 failed，而它的结论写着"验证完成 ✅"）。

给某段代码补测试是**合法**的子任务（它产生新文件/新代码），"跑一遍测试看看"不是。

只返回 JSON 数组，不要其他文字。"""
        system = ("你是资深技术负责人，擅长把任务拆成最小可执行、依赖清晰的子任务。"
                  "每个子任务都要落到具体的文件改动上。只返回 JSON 数组。")

        response = await self.llm_client.analyze(prompt, system)
        data = extract_json(response)
        if not isinstance(data, list) or not data:
            raise ValueError("LLM 未返回有效子任务数组")

        # LLM 自定义 id -> 规范 id（按出现顺序），后续依赖据此重映射
        id_map: Dict[str, str] = {}
        norm = []
        for i, it in enumerate(data, 1):
            if not isinstance(it, dict):
                continue
            canonical = f"subtask-{i}"
            if it.get("id") is not None:
                id_map[str(it["id"])] = canonical
            norm.append((i, canonical, it))

        subtasks: List[SubTask] = []
        for idx, canonical, it in norm:
            deps = []
            for d in (it.get("dependencies") or []):
                mapped = id_map.get(str(d))
                # 只保留指向更早子任务的依赖，杜绝自依赖/前向依赖造成环
                if mapped and mapped != canonical and int(mapped.split("-")[1]) < idx:
                    deps.append(mapped)
            subtasks.append(SubTask(
                id=canonical,
                title=(it.get("title") or it.get("description", "")[:40] or canonical),
                description=it.get("description", ""),
                required_capabilities=it.get("required_capabilities") or [],
                dependencies=deps,
                acceptance_criteria=it.get("acceptance_criteria") or [],
            ))

        if not subtasks:
            raise ValueError("LLM 子任务为空")
        return subtasks

    def _create_subtask(
        self, 
        title: str, 
        description: str,
        required_capabilities: Optional[List[str]] = None,
        dependencies: Optional[List[str]] = None
    ) -> SubTask:
        """创建子任务"""
        self.task_counter += 1
        return SubTask(
            id=f"subtask-{self.task_counter}",
            title=title,
            description=description,
            required_capabilities=required_capabilities or [],
            dependencies=dependencies or []
        )
