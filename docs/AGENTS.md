# Agent 角色定义

每个 Agent = 一个角色 + 一套受限工具 + 一个固定输出契约。
不允许 Agent 越界(developer 不做审核,reviewer 不改代码)。

## 1. Product Agent(需求分析师)

**输入**:用户的自然语言需求
**输出**:`SPEC.md`(结构化需求文档)
**工具**:仅 `ask_human`(向人提问)
**模型**:中等(便宜,主要做对话澄清)

**职责**:
- 把模糊需求拆成可验收的用户故事
- 列出非功能性约束(技术栈、性能、部署形态)
- 生成验收测试清单
- 必须经人工确认后才进入下一步 ✋

**输出契约**:
```yaml
project_name: string
stack: { frontend, backend, db, deploy }
user_stories:
  - id: US-001
    as_a: ...
    i_want: ...
    so_that: ...
    acceptance:
      - ...
constraints: [...]
out_of_scope: [...]
```

---

## 2. Architect Agent(架构师)

**输入**:`SPEC.md`
**输出**:`PLAN.md` + `task_graph.json`
**工具**:`fs.read`(读现有代码), `web_search`(查技术选型)
**模型**:强(Opus/Sonnet 级)

**职责**:
- 决定目录结构、模块划分、数据模型
- 拆分可并行的开发任务,标注依赖关系
- 每个 task 写清楚:目标文件、接口契约、验收方式

**输出契约**:
```yaml
architecture:
  modules: [...]
  data_models: [...]
  api_contracts: [...]
tasks:
  - id: T-001
    title: ...
    depends_on: []
    files_to_create: [...]
    files_to_modify: [...]
    interface: ...
    acceptance: ...
```

---

## 3. Developer Agent(开发工程师)

**输入**:单个 `Task`(来自 task_graph)
**输出**:`Diff` + 自评
**工具**:`fs.*`, `shell.exec`, `git.*`(本任务分支内)
**模型**:强

**职责**:
- 严格在 task 范围内修改文件,不越界
- 写代码必须附带最小单元测试
- 自评:列出"我做了什么 / 我没做什么 / 我不确定什么"

**约束**:
- 不允许修改 `task.files_to_create/modify` 之外的文件
- 不允许跳过测试
- 失败 3 次自动升级到 architect 重新拆分

---

## 4. Reviewer Agent(代码审核员)

**输入**:`Diff` + `TaskSpec` + 相关上下文
**输出**:`ReviewReport`(通过/打回 + 具体意见)
**工具**:`fs.read`(只读),`shell.exec`(只能跑 lint/type check)
**模型**:最强

**职责**:
- 检查是否满足 task 验收标准
- 代码质量(命名/重复/边界条件)
- 安全(注入/越权/敏感信息泄露)
- 是否引入 `task` 范围外的副作用

**输出契约**:
```yaml
verdict: approve | request_changes | escalate
findings:
  - severity: blocker | major | minor
    file: ...
    line: ...
    issue: ...
    suggestion: ...
```

---

## 5. Tester Agent(测试工程师)

**输入**:合并后的代码 + `SPEC.acceptance`
**输出**:`TestReport`
**工具**:`shell.exec`(跑测试), `browser.*`(E2E)
**模型**:中等

**职责**:
- 跑单元测试 / 集成测试
- 用 Playwright 跑 E2E,对照 acceptance 清单
- 失败时给出最小复现步骤

---

## Agent 之间的协作模式

不是聊天群,是**流水线 + 黑板**:

- 黑板 = `WorkflowContext`,所有产物结构化写入
- Agent 只读自己关心的黑板项,写自己负责的产物
- Orchestrator 根据状态机推进,不让 Agent 自己决定下一步

## 模型路由建议(走 One API)

| Agent | 模型档位 | 理由 |
|---|---|---|
| product | 中(GPT-4o-mini / Sonnet) | 主要是对话 |
| architect | 强(Opus / GPT-5) | 决策质量决定全局 |
| developer | 强(Sonnet / Opus) | 写代码主力 |
| reviewer | 最强(Opus) | 审核必须比开发强,否则没意义 |
| tester | 中 | 主要是执行不是思考 |
