"""多 Agent 协作系统 - 子 Agent 深入探索"""

import asyncio
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """任务类型"""
    REQUIREMENT = "requirement"      # 需求分析
    ARCHITECTURE = "architecture"    # 架构设计
    IMPLEMENTATION = "implementation"  # 代码实现
    TESTING = "testing"              # 测试
    REVIEW = "review"                # 代码审查
    INTEGRATION = "integration"      # 集成
    DOCUMENTATION = "documentation"  # 文档


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class CollaborationTask(BaseModel):
    """协作任务"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str
    description: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list)
    output: Any = None
    artifacts: List[str] = Field(default_factory=list)  # 产出的文件
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


class AgentTeam(BaseModel):
    """Agent 团队"""
    team_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    goal: str
    agents: Dict[str, Any] = Field(default_factory=dict)
    tasks: List[CollaborationTask] = Field(default_factory=list)
    shared_context: Dict[str, Any] = Field(default_factory=dict)
    learnings: List[str] = Field(default_factory=list)


class SubAgentWorker:
    """子 Agent 工作者"""
    
    def __init__(
        self,
        agent_id: str,
        role: str,
        agent: Any,
        capabilities: List[str]
    ):
        self.agent_id = agent_id
        self.role = role
        self.agent = agent
        self.capabilities = capabilities
        self.current_task: Optional[CollaborationTask] = None
        self.completed_tasks: List[str] = []
        self.busy = False
    
    async def execute_task(
        self, 
        task: CollaborationTask,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """执行任务"""
        self.busy = True
        self.current_task = task
        task.status = TaskStatus.IN_PROGRESS
        task.assigned_agent = self.agent_id
        
        try:
            # 构建执行提示
            prompt = self._build_prompt(task, context)
            
            # 执行
            result = await self.agent.execute(prompt)
            
            # 更新任务状态
            task.status = TaskStatus.COMPLETED
            task.output = result.output
            task.completed_at = datetime.now()
            self.completed_tasks.append(task.id)
            
            return {
                "success": True,
                "output": result.output,
                "artifacts": getattr(result, 'files_created', [])
            }
        
        except Exception as e:
            task.status = TaskStatus.FAILED
            return {
                "success": False,
                "error": str(e)
            }
        
        finally:
            self.busy = False
            self.current_task = None
    
    def _build_prompt(
        self, 
        task: CollaborationTask,
        context: Dict[str, Any]
    ) -> str:
        """构建执行提示"""
        # 获取相关上下文
        related_outputs = context.get("task_outputs", {})
        dependencies_output = "\n".join(
            f"--- {dep_id} ---\n{related_outputs.get(dep_id, '无')}"
            for dep_id in task.dependencies
            if dep_id in related_outputs
        )
        
        return f"""## 任务信息

**目标**: {context.get('goal', '未定义')}
**任务**: {task.title}
**类型**: {task.task_type.value}
**描述**: {task.description}

## 依赖任务输出

{dependencies_output if dependencies_output else "无依赖"}

## 团队学习

{chr(10).join(f"- {l}" for l in context.get('learnings', [])[-5:]) or "暂无"}

## 要求

1. 高质量完成任务
2. 输出清晰的结构化内容
3. 如果需要创建文件，明确列出文件路径和内容
4. 记录关键决策和学习点
"""


class MultiAgentCollaborator:
    """多 Agent 协作系统"""
    
    def __init__(
        self,
        goal: str,
        max_iterations: int = 50,
        max_concurrent: int = 3
    ):
        self.goal = goal
        self.max_iterations = max_iterations
        self.max_concurrent = max_concurrent
        
        # 团队
        self.team = AgentTeam(name="dev-team", goal=goal)
        
        # 子 Agent 池
        self.workers: Dict[str, SubAgentWorker] = {}
        
        # 任务队列
        self.task_queue: asyncio.Queue = asyncio.Queue()
        
        # 共享上下文
        self.shared_context: Dict[str, Any] = {
            "goal": goal,
            "task_outputs": {},
            "learnings": [],
            "artifacts": []
        }
        
        # 统计
        self.iterations = 0
        self.start_time = 0.0
    
    def add_worker(
        self,
        role: str,
        agent: Any,
        capabilities: List[str]
    ) -> str:
        """添加子 Agent"""
        worker_id = f"{role}-{uuid.uuid4().hex[:4]}"
        worker = SubAgentWorker(
            agent_id=worker_id,
            role=role,
            agent=agent,
            capabilities=capabilities
        )
        self.workers[worker_id] = worker
        self.team.agents[worker_id] = {
            "role": role,
            "capabilities": capabilities
        }
        logger.info(f"Added worker: {worker_id} ({role})")
        return worker_id
    
    async def run(self) -> Dict[str, Any]:
        """运行协作"""
        self.start_time = asyncio.get_event_loop().time()
        
        logger.info(f"Starting collaboration for goal: {self.goal}")
        
        # 1. 需求分析
        requirements = await self._analyze_requirements()
        
        # 2. 生成任务计划
        tasks = await self._generate_tasks(requirements)
        self.team.tasks = tasks
        
        # 3. 执行任务（支持并行）
        results = await self._execute_tasks(tasks)
        
        # 4. 集成和审查
        final_result = await self._integrate_and_review(results)
        
        return {
            "goal": self.goal,
            "status": "completed" if final_result.get("success") else "failed",
            "iterations": self.iterations,
            "tasks_completed": len([t for t in tasks if t.status == TaskStatus.COMPLETED]),
            "artifacts": self.shared_context.get("artifacts", []),
            "learnings": self.shared_context.get("learnings", []),
            "final_output": final_result
        }
    
    async def _analyze_requirements(self) -> Dict[str, Any]:
        """需求分析"""
        logger.info("Phase 1: Analyzing requirements...")
        
        # 找到需求分析 Agent
        requirement_worker = self._find_worker_by_role("product")
        if not requirement_worker:
            requirement_worker = self._find_worker_by_role("architect")
        if not requirement_worker:
            # 使用第一个可用的 worker
            requirement_worker = list(self.workers.values())[0] if self.workers else None
        
        if not requirement_worker:
            raise ValueError("No workers available")
        
        # 创建需求分析任务
        task = CollaborationTask(
            title="需求分析",
            description=f"分析目标: {self.goal}，生成详细的需求规格",
            task_type=TaskType.REQUIREMENT
        )
        
        # 执行
        result = await requirement_worker.execute_task(task, self.shared_context)
        
        if result.get("success"):
            self.shared_context["requirements"] = result.get("output")
            self.shared_context["task_outputs"][task.id] = result.get("output")
        
        return {"requirements": result.get("output", "")}
    
    async def _generate_tasks(
        self, 
        requirements: Dict[str, Any]
    ) -> List[CollaborationTask]:
        """生成任务计划"""
        logger.info("Phase 2: Generating task plan...")
        
        # 找到架构师 Agent
        architect_worker = self._find_worker_by_role("architect")
        if not architect_worker:
            architect_worker = list(self.workers.values())[0]
        
        # 创建架构设计任务
        task = CollaborationTask(
            title="架构设计与任务分解",
            description=f"基于需求，设计架构并分解为可执行任务",
            task_type=TaskType.ARCHITECTURE
        )
        
        result = await architect_worker.execute_task(
            task, 
            {**self.shared_context, "requirements": requirements.get("requirements", "")}
        )
        
        if result.get("success"):
            # 解析任务列表
            tasks = self._parse_tasks(result.get("output", ""))
            self.shared_context["task_outputs"][task.id] = result.get("output")
            return tasks
        
        # 默认任务
        return self._generate_default_tasks()
    
    def _parse_tasks(self, output: str) -> List[CollaborationTask]:
        """解析任务列表"""
        tasks = []
        
        # 简单解析（实际应用中可以更复杂）
        lines = str(output).split('\n')
        current_task = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 检测任务标题
            if any(line.startswith(prefix) for prefix in ['1.', '2.', '3.', '4.', '5.', '-', '*']):
                if current_task:
                    tasks.append(current_task)
                
                # 确定任务类型
                task_type = TaskType.IMPLEMENTATION
                if any(kw in line.lower() for kw in ['需求', 'requirement']):
                    task_type = TaskType.REQUIREMENT
                elif any(kw in line.lower() for kw in ['架构', 'design']):
                    task_type = TaskType.ARCHITECTURE
                elif any(kw in line.lower() for kw in ['测试', 'test']):
                    task_type = TaskType.TESTING
                elif any(kw in line.lower() for kw in ['审查', 'review']):
                    task_type = TaskType.REVIEW
                
                current_task = CollaborationTask(
                    title=line.lstrip('0123456789.-* '),
                    description="",
                    task_type=task_type
                )
            
            elif current_task:
                current_task.description += line + "\n"
        
        if current_task:
            tasks.append(current_task)
        
        # 设置依赖关系
        for i, task in enumerate(tasks):
            if i > 0:
                task.dependencies.append(tasks[i-1].id)
        
        return tasks if tasks else self._generate_default_tasks()
    
    def _generate_default_tasks(self) -> List[CollaborationTask]:
        """生成默认任务"""
        return [
            CollaborationTask(
                title="需求分析",
                description="分析并明确需求",
                task_type=TaskType.REQUIREMENT
            ),
            CollaborationTask(
                title="架构设计",
                description="设计系统架构",
                task_type=TaskType.ARCHITECTURE,
                dependencies=[]  # 会在后面设置
            ),
            CollaborationTask(
                title="核心实现",
                description="实现核心功能",
                task_type=TaskType.IMPLEMENTATION
            ),
            CollaborationTask(
                title="测试验证",
                description="编写和运行测试",
                task_type=TaskType.TESTING
            ),
            CollaborationTask(
                title="代码审查",
                description="审查代码质量",
                task_type=TaskType.REVIEW
            )
        ]
    
    async def _execute_tasks(
        self, 
        tasks: List[CollaborationTask]
    ) -> Dict[str, Any]:
        """执行任务（支持并行）"""
        logger.info("Phase 3: Executing tasks...")
        
        results = {}
        completed_tasks: Set[str] = set()
        
        while len(completed_tasks) < len(tasks):
            self.iterations += 1
            
            if self.iterations > self.max_iterations:
                logger.warning("Max iterations reached")
                break
            
            # 找出可执行的任务
            ready_tasks = [
                t for t in tasks
                if t.status == TaskStatus.PENDING
                and all(dep in completed_tasks for dep in t.dependencies)
            ]
            
            if not ready_tasks:
                # 检查是否有进行中的任务
                in_progress = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
                if not in_progress:
                    logger.warning("No ready tasks and no tasks in progress")
                    break
                # 等待进行中的任务
                await asyncio.sleep(1)
                continue
            
            # 并行执行（最多 max_concurrent 个）
            batch = ready_tasks[:self.max_concurrent]
            
            # 找到合适的 worker
            coroutines = []
            for task in batch:
                worker = self._find_best_worker(task)
                if worker:
                    coroutines.append(
                        self._execute_single_task(worker, task)
                    )
            
            # 并行执行
            if coroutines:
                batch_results = await asyncio.gather(*coroutines, return_exceptions=True)
                
                for task, result in zip(batch, batch_results):
                    if isinstance(result, Exception):
                        results[task.id] = {"success": False, "error": str(result)}
                        task.status = TaskStatus.FAILED
                    else:
                        results[task.id] = result
                        if result.get("success"):
                            completed_tasks.add(task.id)
                            # 更新共享上下文
                            self.shared_context["task_outputs"][task.id] = result.get("output")
                            if result.get("artifacts"):
                                self.shared_context["artifacts"].extend(result["artifacts"])
            
            await asyncio.sleep(0.5)
        
        return results
    
    async def _execute_single_task(
        self,
        worker: SubAgentWorker,
        task: CollaborationTask
    ) -> Dict[str, Any]:
        """执行单个任务"""
        logger.info(f"Executing task: {task.title} with {worker.agent_id}")
        return await worker.execute_task(task, self.shared_context)
    
    def _find_best_worker(self, task: CollaborationTask) -> Optional[SubAgentWorker]:
        """找到最合适的 worker"""
        # 根据任务类型找 worker
        role_mapping = {
            TaskType.REQUIREMENT: ["product", "architect"],
            TaskType.ARCHITECTURE: ["architect", "developer"],
            TaskType.IMPLEMENTATION: ["developer"],
            TaskType.TESTING: ["tester", "developer"],
            TaskType.REVIEW: ["reviewer", "architect"],
            TaskType.INTEGRATION: ["developer", "architect"],
            TaskType.DOCUMENTATION: ["product", "developer"]
        }
        
        preferred_roles = role_mapping.get(task.task_type, ["developer"])
        
        # 先找空闲的 worker
        for role in preferred_roles:
            for worker in self.workers.values():
                if worker.role == role and not worker.busy:
                    return worker
        
        # 找任意空闲的 worker
        for worker in self.workers.values():
            if not worker.busy:
                return worker
        
        return None
    
    def _find_worker_by_role(self, role: str) -> Optional[SubAgentWorker]:
        """根据角色找 worker"""
        for worker in self.workers.values():
            if worker.role == role:
                return worker
        return None
    
    async def _integrate_and_review(
        self, 
        results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """集成和审查"""
        logger.info("Phase 4: Integration and review...")
        
        # 找到审查 Agent
        reviewer = self._find_worker_by_role("reviewer")
        if not reviewer:
            reviewer = list(self.workers.values())[0]
        
        # 创建审查任务
        task = CollaborationTask(
            title="集成审查",
            description="审查所有任务输出，确保质量和一致性",
            task_type=TaskType.REVIEW
        )
        
        # 收集所有输出
        all_outputs = "\n\n".join(
            f"=== {task_id} ===\n{output.get('output', '')}"
            for task_id, output in results.items()
            if output.get("success")
        )
        
        result = await reviewer.execute_task(
            task,
            {
                **self.shared_context,
                "all_outputs": all_outputs
            }
        )
        
        return {
            "success": result.get("success", False),
            "review": result.get("output", ""),
            "all_task_results": results
        }


class DevelopmentPipeline:
    """完整开发链路"""
    
    def __init__(self, goal: str):
        self.goal = goal
        self.collaborator = MultiAgentCollaborator(goal)
    
    def setup_team(self) -> None:
        """设置开发团队"""
        from src.agents import (
            ProductAgent,
            ArchitectAgent,
            DeveloperAgent,
            ReviewerAgent,
            TesterAgent
        )
        
        # 添加各类 Agent
        self.collaborator.add_worker("product", ProductAgent(), ["requirements", "user_stories"])
        self.collaborator.add_worker("architect", ArchitectAgent(), ["architecture", "design"])
        self.collaborator.add_worker("developer", DeveloperAgent(), ["coding", "implementation"])
        self.collaborator.add_worker("tester", TesterAgent(), ["testing", "verification"])
        self.collaborator.add_worker("reviewer", ReviewerAgent(), ["review", "quality"])
    
    async def run(self) -> Dict[str, Any]:
        """运行完整开发流程"""
        self.setup_team()
        return await self.collaborator.run()
