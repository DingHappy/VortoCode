# 技能系统设计

## 概述

技能系统使 vortocode 能够定义、发现和执行可重用的工作流。技能是模块化的知识单元，可以组合使用，大大提高了系统的灵活性和可扩展性。

## 技能定义格式

### SKILL.md 文件格式

```yaml
---
# 元数据
name: api-developer
description: 实现 RESTful API 端点，遵循团队约定
version: "1.0.0"
author: vortocode

# 调用控制
disable-model-invocation: false  # 是否禁止自动调用
user-invocable: true  # 是否用户可调用

# 能力声明
capabilities:
  - api_development
  - database_design
  - testing

# 所需工具
tools:
  - Read
  - Write
  - Bash
  - Glob

# 所需技能（依赖）
skills:
  - code-style-guide
  - testing-conventions

# 执行上下文
context: fork  # 是否在子代理中执行
agent: developer  # 使用的代理类型

# 参数定义
arguments:
  - name: endpoint
    description: API 端点路径
    required: true
  - name: method
    description: HTTP 方法
    required: false
    default: "GET"

# 模型配置
model: inherit  # 继承主会话模型
effort: high

# Hooks
hooks:
  pre_execute:
    - type: command
      command: "echo 'Starting API development...'"
  post_execute:
    - type: command
      command: "echo 'API development completed.'"
---

## 任务描述

实现 API 端点 `$endpoint`，HTTP 方法为 `$method`。

## 执行步骤

### 1. 需求分析

- 阅读 API 文档
- 理解请求/响应格式
- 确认业务逻辑

### 2. 数据模型设计

- 设计数据库表结构
- 创建 Pydantic 模型
- 定义 API 契约

### 3. 实现端点

- 创建路由处理函数
- 实现业务逻辑
- 添加输入验证
- 处理错误情况

### 4. 编写测试

- 单元测试
- 集成测试
- 边界条件测试

### 5. 文档更新

- 更新 API 文档
- 添加使用示例

## 约束条件

- 遵循 RESTful 设计原则
- 使用标准 HTTP 状态码
- 返回一致的错误格式
- 包含请求验证
- 记录关键日志

## 验收标准

- [ ] 端点正常工作
- [ ] 测试通过
- [ ] 代码符合规范
- [ ] 文档已更新

## 参考资料

- [API 设计指南](./references/api-design.md)
- [错误处理规范](./references/error-handling.md)
```

## 核心组件

### 1. Skill (技能基类)

```python
from typing import Dict, List, Any, Optional
from pydantic import BaseModel
from enum import Enum

class SkillMetadata(BaseModel):
    """技能元数据"""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    capabilities: List[str] = []
    tools: List[str] = []
    skills: List[str] = []  # 依赖的技能
    context: Optional[str] = None  # fork 或 None
    agent: Optional[str] = None
    model: str = "inherit"
    effort: str = "medium"
    disable_model_invocation: bool = False
    user_invocable: bool = True

class SkillArgument(BaseModel):
    """技能参数"""
    name: str
    description: str
    required: bool = True
    default: Any = None

class SkillHook(BaseModel):
    """技能 Hook"""
    type: str  # command, http, prompt
    command: Optional[str] = None
    url: Optional[str] = None
    prompt: Optional[str] = None

class Skill(BaseModel):
    """技能"""
    metadata: SkillMetadata
    arguments: List[SkillArgument] = []
    instructions: str
    hooks: Dict[str, List[SkillHook]] = {}
    
    # 运行时状态
    _loaded: bool = False
    _context: Dict[str, Any] = {}
    
    class Config:
        arbitrary_types_allowed = True
    
    async def execute(
        self, 
        args: Dict[str, Any] = None,
        context: Dict[str, Any] = None
    ) -> 'SkillResult':
        """执行技能"""
        # 1. 验证参数
        validated_args = self._validate_args(args or {})
        
        # 2. 执行 pre hooks
        await self._execute_hooks("pre_execute", validated_args)
        
        # 3. 渲染指令
        rendered_instructions = self._render_instructions(
            validated_args, context
        )
        
        # 4. 执行技能逻辑
        result = await self._execute_logic(
            rendered_instructions, validated_args, context
        )
        
        # 5. 执行 post hooks
        await self._execute_hooks("post_execute", result)
        
        return result
    
    def _validate_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """验证参数"""
        validated = {}
        
        for arg_def in self.arguments:
            if arg_def.name in args:
                validated[arg_def.name] = args[arg_def.name]
            elif arg_def.required:
                raise ValueError(f"缺少必需参数: {arg_def.name}")
            else:
                validated[arg_def.name] = arg_def.default
        
        return validated
    
    def _render_instructions(
        self, 
        args: Dict[str, Any],
        context: Dict[str, Any] = None
    ) -> str:
        """渲染指令"""
        instructions = self.instructions
        
        # 替换参数占位符
        for key, value in args.items():
            placeholder = f"${key}"
            instructions = instructions.replace(placeholder, str(value))
        
        # 替换特殊变量
        if context:
            for key, value in context.items():
                placeholder = f"${{{key}}}"
                instructions = instructions.replace(placeholder, str(value))
        
        return instructions
    
    async def _execute_logic(
        self, 
        instructions: str,
        args: Dict[str, Any],
        context: Dict[str, Any] = None
    ) -> 'SkillResult':
        """执行技能逻辑（由子类实现）"""
        raise NotImplementedError
    
    async def _execute_hooks(
        self, 
        event: str, 
        data: Any
    ):
        """执行 hooks"""
        hooks = self.hooks.get(event, [])
        for hook in hooks:
            if hook.type == "command":
                await self._execute_command_hook(hook.command, data)
            elif hook.type == "http":
                await self._execute_http_hook(hook.url, data)
    
    async def _execute_command_hook(self, command: str, data: Any):
        """执行命令 hook"""
        import asyncio
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            print(f"Hook 执行失败: {stderr.decode()}")
    
    async def _execute_http_hook(self, url: str, data: Any):
        """执行 HTTP hook"""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data) as response:
                if response.status != 200:
                    print(f"Hook 请求失败: {response.status}")


class SkillResult(BaseModel):
    """技能执行结果"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    artifacts: List[str] = []  # 生成的文件路径
    metrics: Dict[str, Any] = {}  # 执行指标
```

### 2. SkillRegistry (技能注册表)

```python
import os
import yaml
from typing import Dict, List, Optional
from pathlib import Path

class SkillRegistry:
    """技能注册表"""
    
    def __init__(self, skill_dirs: List[str] = None):
        self.skill_dirs = skill_dirs or [
            ".vortocode/skills",
            "~/.vortocode/skills"
        ]
        self.skills: Dict[str, Skill] = {}
        self._discover_skills()
    
    def _discover_skills(self):
        """发现并加载技能"""
        for skill_dir in self.skill_dirs:
            skill_path = Path(skill_dir).expanduser()
            if skill_path.exists():
                self._load_skills_from_directory(skill_path)
    
    def _load_skills_from_directory(self, directory: Path):
        """从目录加载技能"""
        for skill_file in directory.rglob("SKILL.md"):
            try:
                skill = self._load_skill_file(skill_file)
                self.register(skill)
                print(f"已加载技能: {skill.metadata.name}")
            except Exception as e:
                print(f"加载技能失败 {skill_file}: {e}")
    
    def _load_skill_file(self, file_path: Path) -> Skill:
        """加载技能文件"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析 frontmatter 和内容
        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError("无效的技能文件格式")
        
        # 解析 YAML frontmatter
        frontmatter = yaml.safe_load(parts[1])
        
        # 解析指令内容
        instructions = parts[2].strip()
        
        # 创建技能对象
        metadata = SkillMetadata(
            name=frontmatter.get("name", file_path.parent.name),
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
        
        arguments = [
            SkillArgument(**arg) 
            for arg in frontmatter.get("arguments", [])
        ]
        
        hooks = {}
        for event, hook_list in frontmatter.get("hooks", {}).items():
            hooks[event] = [SkillHook(**hook) for hook in hook_list]
        
        return Skill(
            metadata=metadata,
            arguments=arguments,
            instructions=instructions,
            hooks=hooks
        )
    
    def register(self, skill: Skill):
        """注册技能"""
        self.skills[skill.metadata.name] = skill
    
    def unregister(self, skill_name: str):
        """注销技能"""
        if skill_name in self.skills:
            del self.skills[skill_name]
    
    def get(self, skill_name: str) -> Optional[Skill]:
        """获取技能"""
        return self.skills.get(skill_name)
    
    def list_skills(
        self, 
        capability: str = None,
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
            if score > 0.5:  # 匹配阈值
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
```

### 3. SkillExecutor (技能执行器)

```python
class SkillExecutor:
    """技能执行器"""
    
    def __init__(
        self, 
        registry: SkillRegistry,
        agent_factory: 'AgentFactory' = None
    ):
        self.registry = registry
        self.agent_factory = agent_factory
    
    async def execute(
        self, 
        skill_name: str,
        args: Dict[str, Any] = None,
        context: Dict[str, Any] = None
    ) -> SkillResult:
        """执行技能"""
        # 获取技能
        skill = self.registry.get(skill_name)
        if not skill:
            raise SkillNotFoundError(skill_name)
        
        # 检查是否需要在子代理中执行
        if skill.metadata.context == "fork":
            return await self._execute_in_subagent(skill, args, context)
        else:
            return await self._execute_inline(skill, args, context)
    
    async def _execute_inline(
        self, 
        skill: Skill,
        args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> SkillResult:
        """内联执行技能"""
        # 直接执行技能逻辑
        return await skill.execute(args, context)
    
    async def _execute_in_subagent(
        self, 
        skill: Skill,
        args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> SkillResult:
        """在子代理中执行技能"""
        if not self.agent_factory:
            raise RuntimeError("未配置 AgentFactory")
        
        # 创建子代理
        agent_type = skill.metadata.agent or "general-purpose"
        agent = self.agent_factory.create_agent(
            agent_type=agent_type,
            tools=skill.metadata.tools,
            model=skill.metadata.model
        )
        
        # 准备任务描述
        task = skill._render_instructions(args, context)
        
        # 执行任务
        result = await agent.execute(task)
        
        return SkillResult(
            success=result.success,
            output=result.output,
            error=result.error,
            artifacts=result.files_modified,
            metrics={
                "tokens_used": result.tokens_used,
                "duration": result.duration
            }
        )
    
    async def execute_chain(
        self, 
        skill_names: List[str],
        initial_args: Dict[str, Any] = None
    ) -> List[SkillResult]:
        """执行技能链"""
        results = []
        current_args = initial_args or {}
        
        for skill_name in skill_names:
            result = await self.execute(skill_name, current_args)
            results.append(result)
            
            # 将上一个结果传递给下一个技能
            if result.success:
                current_args["previous_output"] = result.output
            else:
                # 链中断
                break
        
        return results
```

### 4. SkillComposer (技能组合器)

```python
class SkillComposer:
    """技能组合器"""
    
    def __init__(self, registry: SkillRegistry):
        self.registry = registry
    
    def compose(
        self, 
        skill_names: List[str],
        composition_type: str = "sequential"
    ) -> 'CompositeSkill':
        """组合多个技能"""
        skills = []
        for name in skill_names:
            skill = self.registry.get(name)
            if not skill:
                raise SkillNotFoundError(name)
            skills.append(skill)
        
        return CompositeSkill(
            skills=skills,
            composition_type=composition_type
        )


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
            description=f"组合技能: {', '.join(s.metadata.description for s in skills)}",
            capabilities=list(set(
                cap for s in skills for cap in s.metadata.capabilities
            )),
            tools=list(set(
                tool for s in skills for tool in s.metadata.tools
            ))
        )
        
        super().__init__(
            metadata=metadata,
            instructions="",
            hooks={}
        )
    
    async def execute(
        self, 
        args: Dict[str, Any] = None,
        context: Dict[str, Any] = None
    ) -> SkillResult:
        """执行组合技能"""
        if self.composition_type == "sequential":
            return await self._execute_sequential(args, context)
        elif self.composition_type == "parallel":
            return await self._execute_parallel(args, context)
        else:
            raise ValueError(f"不支持的组合类型: {self.composition_type}")
    
    async def _execute_sequential(
        self, 
        args: Dict[str, Any],
        context: Dict[str, Any]
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
                    error=f"技能 {skill.metadata.name} 执行失败: {result.error}"
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
        args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> SkillResult:
        """并行执行"""
        import asyncio
        
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
                error=f"并行执行失败: {errors}"
            )
        
        return SkillResult(
            success=True,
            output=results,
            metrics={"skills_executed": len(results)}
        )
```

## 使用示例

### 1. 定义技能

创建 `.vortocode/skills/code-review/SKILL.md`:

```yaml
---
name: code-review
description: 代码审查，检查代码质量、安全性和最佳实践
capabilities:
  - code_review
  - security_analysis
tools:
  - Read
  - Glob
  - Grep
arguments:
  - name: path
    description: 要审查的代码路径
    required: true
---

## 代码审查任务

审查路径 `$path` 中的代码。

## 审查清单

### 代码质量
- [ ] 命名规范
- [ ] 代码重复
- [ ] 函数复杂度
- [ ] 注释质量

### 安全性
- [ ] 输入验证
- [ ] SQL 注入
- [ ] XSS 攻击
- [ ] 敏感信息泄露

### 最佳实践
- [ ] 错误处理
- [ ] 日志记录
- [ ] 性能考虑
- [ ] 测试覆盖
```

### 2. 使用技能

```python
from vortocode.skills import SkillRegistry, SkillExecutor

# 初始化
registry = SkillRegistry([".vortocode/skills"])
executor = SkillExecutor(registry)

# 执行技能
result = await executor.execute(
    "code-review",
    args={"path": "src/api/"}
)

print(f"审查结果: {result.output}")

# 执行技能链
results = await executor.execute_chain([
    "code-review",
    "fix-issues",
    "run-tests"
], initial_args={"path": "src/api/"})
```

### 3. 组合技能

```python
from vortocode.skills import SkillComposer

composer = SkillRegistry(registry)

# 创建组合技能
full_workflow = composer.compose([
    "analyze-requirements",
    "design-architecture",
    "implement-code",
    "write-tests",
    "code-review"
], composition_type="sequential")

# 执行组合技能
result = await full_workflow.execute({
    "requirement": "实现用户认证功能"
})
```

## 技能发现机制

### 1. 基于能力匹配

```python
# 查找具有特定能力的技能
skills = registry.list_skills(capability="api_development")
```

### 2. 基于任务需求

```python
# 为任务发现相关技能
skills = registry.discover_for_task([
    "REST API",
    "数据库设计",
    "测试"
])
```

### 3. 基于上下文

```python
# 根据当前上下文推荐技能
context = {
    "project_type": "web_app",
    "tech_stack": ["FastAPI", "PostgreSQL"],
    "current_task": "implement_authentication"
}

skills = registry.recommend_for_context(context)
```

## 配置

```yaml
# .vortocode/skills.yaml
skills:
  directories:
    - ".vortocode/skills"
    - "~/.vortocode/skills"
    - "/etc/vortocode/skills"
  
  auto_discovery:
    enabled: true
    scan_interval: 60  # 秒
  
  execution:
    default_context: "inline"  # 或 "fork"
    timeout: 300  # 秒
    max_retries: 3
  
  composition:
    max_chain_length: 10
    parallel_limit: 5
```
