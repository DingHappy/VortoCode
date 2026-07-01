# 架构设计

> ⚠️ **历史设计文档（原始蓝图，非当前实现）**。本文是项目早期的目标架构草案，部分选型**未采用**：
> 实际前端是**原生 HTML/CSS/JS**（非 Next.js）、存储是 **SQLite + JSON 文件 + 可选 Qdrant**（非 Postgres，Redis/PostgreSQL 暂未接入）。
> **当前权威架构以 [README](../README.md) 为准**。本文保留作设计意图/演进记录。

## 一、整体分层

```
┌─────────────────────────────────────────────────────────┐
│  Web Console (Next.js)                                  │
│  需求输入 · 流程可视化 · 人工审核/合并                  │
└───────────────────┬─────────────────────────────────────┘
                    │ REST / WebSocket
┌───────────────────▼─────────────────────────────────────┐
│  Orchestrator (FastAPI + 状态机)                        │
│  - 工作流定义 (YAML/DSL)                                │
│  - 任务状态持久化 (Postgres)                            │
│  - Agent 调度 / 上下文管理 / 事件总线                   │
└───────────┬─────────────────────────────┬───────────────┘
            │                             │
┌───────────▼──────────────┐  ┌───────────▼───────────────┐
│  Agent Workers           │  │  Sandbox Runtime          │
│  product/architect/      │  │  - Docker 隔离容器        │
│  developer/reviewer/     │  │  - 文件 / Shell / Git     │
│  tester                  │  │  - 浏览器 (Playwright)    │
└───────────┬──────────────┘  └───────────────────────────┘
            │
┌───────────▼─────────────────────────────────────────────┐
│  LLM Gateway = 自建 One API 中转站                       │
│  统一鉴权 / 计费 / 模型路由 / 缓存 / 限流                │
└─────────────────────────────────────────────────────────┘
```

## 二、核心工作流(状态机)

```
[需求收集] ──人工确认──> [需求规格 SPEC]
                              │
                              ▼
                       [架构设计 PLAN]
                              │
                              ▼
                       ┌──────────────┐
                       │  任务拆分     │  (架构师产出 task graph)
                       └──────┬───────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         [开发 task₁]    [开发 task₂]    [开发 task₃]   (并行)
              │               │               │
              └───────┬───────┴───────┬───────┘
                      ▼               ▼
                 [代码审核 Review] ◄─修改─┐
                      │                  │
                      ▼                  │
                 [自动测试 Test] ────失败─┘
                      │
                      ▼ (通过)
                 [集成 / 生成 PR]
                      │
                      ▼
                 [人工合并] ◄── 人工介入点 2
```

**关键不变量**:
- 每个状态转移都写库,可重放/回滚
- 任何 Agent 失败,流程进入 `NEEDS_HUMAN` 状态而不是死循环
- 修改→审核循环上限(默认 5 轮),超限自动升级到人工

## 三、Agent 抽象

```python
class Agent(Protocol):
    role: str                          # product / architect / ...
    system_prompt: str
    tools: list[Tool]                  # 受限工具集
    model: str                         # 走 One API 路由

    async def run(
        self,
        task: Task,
        context: WorkflowContext,
    ) -> AgentResult:
        ...
```

**设计原则**:
- Agent 无状态,所有状态在 Orchestrator
- Agent 不互相直接调用,通过事件总线/消息队列
- 每个 Agent 工具集最小化(developer 才有写文件权限,reviewer 只读)

## 四、Sandbox 设计

借鉴 OpenHands 的 runtime 思路,但简化:

- **隔离单位**:每个项目一个 Docker 容器(长期存活,挂载工作区)
- **工具协议**:统一 JSON-RPC,Agent 不直接调 host
  - `fs.read` / `fs.write` / `fs.list`
  - `shell.exec`(超时 + 输出截断)
  - `git.commit` / `git.diff`
  - `browser.navigate` / `browser.screenshot`(Playwright)
- **安全边界**:容器不可访问宿主网络,只能通过 LLM Gateway 出网

## 五、LLM Gateway 集成

所有 Agent 通过 `OPENAI_API_BASE = https://<your-one-api>/v1` 调用,好处:

1. **模型路由**:product 用便宜模型,developer 用强模型,reviewer 用最强模型
2. **成本可见**:每个任务的 token 消耗按 agent 标签归集
3. **降级策略**:主模型失败自动 fallback
4. **Prompt 缓存**:system prompt + 项目上下文复用

## 六、上下文管理(关键难点)

多 Agent 协作最容易爆的就是上下文。策略:

1. **分层上下文**:
   - L1 全局:项目 README / 架构文档(常驻)
   - L2 任务:当前 task 的 spec + 相关文件清单
   - L3 工作记忆:本轮 tool 调用结果(可压缩)

2. **检索而非塞入**:用代码 embedding + 文件树检索,不把全仓塞 prompt

3. **Agent 间传递结构化产物**,不是聊天记录:
   - architect → developer 传的是 `TaskSpec(files, interfaces, tests)`,不是对话历史
   - developer → reviewer 传的是 `Diff + 自评`

## 七、可观测性

- 每次 LLM 调用 → trace(input/output/tokens/latency)
- 每次状态转移 → event log
- Web 控制台实时流式展示
- 失败任务可"回到上一步重试"
