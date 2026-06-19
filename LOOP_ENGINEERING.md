# Claude Code Loop Engineering 思想总结

## 核心概念：Agentic Loop（代理循环）

Claude Code 的核心是一个**代理循环**，包含三个阶段：

```
用户提示 → 收集上下文 → 采取行动 → 验证结果 → 循环直到完成
     ↑                                              |
     └──────────────────────────────────────────────┘
```

### 1. 收集上下文（Gather Context）
- 读取文件、搜索代码库
- 理解项目结构和现有代码
- 获取必要的信息来理解任务

### 2. 采取行动（Take Action）
- 编辑文件、创建代码
- 运行命令、执行测试
- 与外部工具交互

### 3. 验证结果（Verify Results）
- 运行测试检查正确性
- 比较输出与预期
- 收集反馈信息

## Loop Engineering 的核心思想

### 1. 自动化验证循环

**关键原则**: 给 Claude 一个可以自己运行的检查，而不是让人来验证。

```bash
# 不好的做法：需要人工验证
"implement a function that validates email addresses"

# 好的做法：Claude 可以自己验证
"write a validateEmail function. test cases: user@example.com is true, 
invalid is false, user@.com is false. run the tests after implementing"
```

### 2. 闭环反馈系统

Claude 执行任务 → 运行检查 → 读取结果 → 迭代改进 → 直到检查通过

```
┌─────────────────────────────────────────────────────────────┐
│                    Loop Engineering 流程                      │
├─────────────────────────────────────────────────────────────┤
│  1. 定义任务和验证标准                                          │
│  2. Claude 执行任务                                           │
│  3. 运行验证检查（测试、构建、lint）                             │
│  4. 读取验证结果                                               │
│  5. 如果失败，分析原因并修复                                     │
│  6. 重复步骤 3-5 直到通过                                       │
│  7. 任务完成                                                   │
└─────────────────────────────────────────────────────────────┘
```

### 3. 验证层次

Claude Code 提供了多个层次的验证机制：

| 层次 | 方法 | 适用场景 |
|------|------|----------|
| **单次提示** | 在同一个提示中要求运行测试 | 简单任务 |
| **会话级** | 使用 `/goal` 设置验证条件 | 中等复杂度 |
| **确定性门控** | 使用 Stop Hook 运行脚本 | 必须通过的检查 |
| **独立验证** | 使用子代理进行二次验证 | 关键任务 |

### 4. 上下文管理

**核心约束**: 上下文窗口会填满，性能会下降。

```bash
# 管理上下文的策略
- 使用 /clear 在不相关任务之间重置上下文
- 使用子代理进行探索，避免污染主上下文
- 使用 /compact 压缩对话历史
- 将持久规则放在 CLAUDE.md 中
```

## /loop 功能详解

### 基本用法

```bash
# 固定间隔运行
/loop 5m check if the deployment finished

# 让 Claude 选择间隔
/loop check whether CI passed

# 使用内置维护提示
/loop
```

### 动态间隔

当省略间隔时，Claude 会根据观察动态选择延迟：
- 构建正在进行时：短等待
- PR 活跃时：短等待
- 没有挂起的工作时：长等待

### 自定义默认提示

创建 `.claude/loop.md` 文件替换内置维护提示：

```markdown
# .claude/loop.md
Check the `release/next` PR. If CI is red, pull the failing job log,
diagnose, and push a minimal fix. If new review comments have arrived,
address each one and resolve the thread. If everything is green and
quiet, say so in one line.
```

## 最佳实践模式

### 1. 探索→计划→实现

```bash
# 阶段 1: 探索（Plan Mode）
"read /src/auth and understand how we handle sessions"

# 阶段 2: 计划（Plan Mode）
"I want to add Google OAuth. What files need to change? Create a plan."

# 阶段 3: 实现
"implement the OAuth flow from your plan. write tests and run them."

# 阶段 4: 提交
"commit with a descriptive message and open a PR"
```

### 2. Writer/Reviewer 模式

```
Session A (Writer):  "Implement a rate limiter for our API endpoints"
Session B (Reviewer): "Review the rate limiter implementation for edge cases"
Session A:           "Address the review feedback"
```

### 3. Fan-out 模式

```bash
# 生成任务列表
"list all Python files that need migrating"

# 并行执行
for file in $(cat files.txt); do
  claude -p "Migrate $file" --allowedTools "Edit,Bash(git commit *)"
done
```

### 4. 验证子代理模式

```bash
# 实现任务
"implement the rate limiter"

# 独立验证
"use a subagent to review this code for edge cases"
```

## 关键洞察

### 1. 验证是关键

> "Give Claude a check it can run: tests, a build, a screenshot to compare. 
> It's the difference between a session you watch and one you walk away from."

没有验证，你就是验证循环。有验证，循环自己关闭。

### 2. 上下文是最宝贵的资源

> "Claude's context window fills up fast, and performance degrades as it fills."

管理上下文比写好提示更重要。

### 3. 紧密反馈循环

> "The best results come from tight feedback loops."

尽早纠正，频繁纠正。两次失败后，清除上下文重新开始。

### 4. 委托而非指令

> "Think of delegating to a capable colleague. Give context and direction, 
> then trust Claude to figure out the details."

## 在 VortoCode 中的应用

我们可以将 Loop Engineering 思想应用到我们的项目中：

### 1. 增强编排引擎的验证循环

```python
# 在 orchestrator/engine.py 中添加验证循环
async def orchestrate_with_verification(self, task: str):
    result = await self.orchestrate(task)
    
    # 验证循环
    max_attempts = 3
    for attempt in range(max_attempts):
        if await self.verify_result(result):
            return result
        
        # 基于验证反馈重新执行
        result = await self.re_execute_with_feedback(task, result)
    
    return result
```

### 2. 添加 /loop 支持

```python
# 实现定期任务检查
async def loop_check(self, interval: str, check_func: Callable):
    while True:
        result = await check_func()
        if result.success:
            break
        await asyncio.sleep(parse_interval(interval))
```

### 3. 子代理验证

```python
# 使用子代理进行独立验证
async def verify_with_subagent(self, task_result: Any):
    reviewer = SubAgent(role="reviewer")
    review = await reviewer.review(task_result)
    return review
```

## 总结

Loop Engineering 的核心思想是：

1. **自动化验证**: 让 Claude 自己验证工作，而不是人工检查
2. **闭环反馈**: 执行→验证→改进→重复
3. **上下文管理**: 保持上下文清洁，避免性能下降
4. **委托验证**: 使用子代理进行独立验证
5. **持续监控**: 使用 /loop 定期检查状态

这些思想可以应用到任何 AI 代理系统中，包括我们的 VortoCode 框架。
