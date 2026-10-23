# 关键技术决策(ADR)

记录"为什么这么选",方便后续回顾和挑战。

---

## ADR-001:不基于 OpenHands 整体集成,只借鉴其 Sandbox 思路

**选项**:
- A. 直接 fork OpenHands 改造
- B. 引入 OpenHands 作为 sublib
- **C. 自写 Sandbox 层,借鉴其工具协议设计** ✅

**决定**:C

**理由**:
- OpenHands 设计是"单 agent + 重工具",我们要"多 agent + 轻协调",架构不匹配
- 整体集成会引入大量与我们流程冲突的状态管理
- Sandbox/工具协议这一层是它最有价值的部分,可以参考实现
- 自写更可控,LLM 调用走自己的网关也更顺

**代价**:重复造一部分轮子(约 2 周工作量)

---

## ADR-002:工作流引擎自写,不用 LangGraph / Temporal

**选项**:
- A. LangGraph(Agent 工作流主流方案)
- B. Temporal(企业级)
- **C. 自写状态机 + Postgres 持久化** ✅

**决定**:C(第一版),Phase 2 后视情况迁移到 LangGraph

**理由**:
- LangGraph 抽象偏 Agent 聊天,对"严格状态机 + 人工介入"支持不够直接
- Temporal 重,小团队不值得
- 自写一个基于 Postgres 的简单 FSM 几百行代码,完全够 MVP

**风险**:并发/分布式扩展时可能要重写

---

## ADR-003:LLM 调用统一走自建 One API 网关

**决定**:所有 Agent 通过 `OPENAI_API_BASE` 指向自己的 One API 实例

**理由**:
- 成本可见(按 agent 维度 tag)
- 模型路由灵活(开发用 Sonnet,审核切 Opus)
- Prompt 缓存复用
- 与已有 [[ai-token-relay-direction]] 项目协同

**注意**:One API 网关本身要先稳定,这是 hard dependency

---

## ADR-004:第一版只做"从零生成新项目",不做"修改已有项目"

**理由**:
- 已有项目要做代码索引、依赖分析、影响范围分析,复杂度高一个量级
- MVP 阶段先证明"端到端能跑通",再扩展
- Phase 2 再加增量开发能力

**反对意见**:从零生成是 toy 场景,真实价值在改已有项目
**回应**:认可,但先把流程跑通再加难度

---

## ADR-005:Agent 之间通过结构化产物通信,不传聊天历史

**决定**:Agent 输出严格 schema(Pydantic),不允许自由对话

**理由**:
- 聊天历史会越滚越长,上下文爆炸
- 结构化产物可以被工作流引擎理解 / 校验 / 回放
- 跨 Agent 错误更容易定位

**代价**:灵活性降低,某些复杂协作需要绕路

---

## ADR-006:Sandbox 用 Docker,不用 Firecracker / gVisor

**理由**:个人项目,Docker 足够;真要做产品再升级

---

## ADR-007:前端用 Next.js,后端 Orchestrator 用 FastAPI

**理由**:
- 与目标产物技术栈一致(吃自己的狗粮)
- 与 [[trade-prospector-project]] 栈对齐,经验复用
- FastAPI 异步友好,适合长连接流式

---

## ADR-008：冻结任务协作的高级功能，保留隔离开发流水线核心（2026-10-22）

**背景**：
- Code 侧（任务协作台账、工作台、隔离开发流水线及 Desktop 对应面板）超过 1.5 万行，约占后端四成。
- 实际使用：常驻电脑上两个月只跑过 1 次开发计划；助理派活两周 1 张任务单。写代码已交给 Mac 上的 Codex，VortoCode 每天真在用的是常驻助理与 Codex 监管。
- 2026-10-22 用 `python -m evals` 跑 10 个固定场景（mimo-v2.5-pro，HEAD a8ed403）：落地、诚实、干净都是 100%，共 6 分 22 秒、约 45 万 token。流水线质量没问题，问题是没人用。

**选项**：
- A. 维持现状，继续按任务协作 PRD 往下做
- **B. 保留流水线核心，冻结任务协作的高级功能** ✅
- C. 连流水线一起收缩

**决定**：B

- **保留并照常维护**：隔离开发流水线（`dev_auto` / `dev_isolated` / `dev_resume`、worktree、验证、落 `vorto/*` 分支）、助理派活（`assistant_dispatch`、`dispatch_stats`、`attention`、`decisions`）、后台任务台账基础（`tasks`、`handoffs`、`followups`、`dev_graph`）。
- **冻结**：任务协作的高级扩展——覆盖记录扫描、归档与退役（`task_scan`）、反向发现与依赖接力（`task_dependents`、`task_dependencies`）、任务链执行额度（`task_chain_budget`）、自动检查授权与报告（`continuations`、`continuation_checks`、`check_reports`）、模型证据与对比（`model_evidence`、`model_comparison`）、跨 runtime 收件箱与看板投影（`runtime_inbox`、`dashboard`）。
- 冻结的含义：**不加新功能**；安全问题和明确的 bug 照修；内核重构需要时可以跟着改接口。暂不删除，等确认没有依赖、也确实没用后再单独提删除。

**理由**：维护成本花在没人用的地方，挤占了每天在用的助理和 Codex 监管。流水线核心质量好、体量小，又是派活的执行引擎，留着成本低。

**重新评估的条件**：派活或 Desktop Code 模式的真实使用明显增加（例如一周超过 5 张任务单），或用户明确要做团队协作。

---

## 待决策(占位)

- [ ] **代码 embedding 用什么**?bge-m3 自部署 / OpenAI text-embedding-3 / Voyage
- [ ] **状态存储**:Postgres 是否够,要不要加 Redis(事件总线)
- [ ] **是否支持 GitHub 集成**:直接开 PR 还是只输出 diff
- [ ] **多人协作模型**:单用户单项目,还是多用户共享 agent 池
