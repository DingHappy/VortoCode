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

## 待决策(占位)

- [ ] **代码 embedding 用什么**?bge-m3 自部署 / OpenAI text-embedding-3 / Voyage
- [ ] **状态存储**:Postgres 是否够,要不要加 Redis(事件总线)
- [ ] **是否支持 GitHub 集成**:直接开 PR 还是只输出 diff
- [ ] **多人协作模型**:单用户单项目,还是多用户共享 agent 池
