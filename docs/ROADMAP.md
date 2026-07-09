# 开发路线图

> 2026-07 更新：早期阶段规划保留为历史参考；当前产品方向是把同一个 VortoCode agent runtime
> 拆成 **CLI 版、Desktop 版、Web 服务版** 三种入口，避免三套实现各自分叉。

## 产品版本路线

### CLI 版（开发者本机入口）

定位：最快可用、最贴近 Git 工作流的本机开发工具。

- `vc tui` / `vc agent` / `vc server` 继续作为核心入口
- plan/build 权限门、写盘确认、工具审计、token/context 使用量保持一等能力
- 默认接入 VortoCode Relay + 国产基础模型，用户填 key 后即可跑；同时保留 OpenAI 兼容网关替换能力
- 优先打磨 session resume、diff review、PR/branch 自动化、slash commands、project memory

### Desktop 版（本地常驻工作台）

定位：把 CLI 能力变成可视化、本地优先的桌面协作工作台。

- 会话列表、摘要、恢复、跨项目切换
- diff review、hunk 级 accept/reject、权限确认、工具审计可视化
- artifact/gallery、项目记忆、后台任务、通知中心
- 本机工作区和用户权限仍是边界，不默认上传代码

### Web 服务版（团队/托管入口）

定位：类「龙虾」的浏览器服务形态，适合团队共享和托管运行。

- 多项目、多会话、多用户队列和权限分层
- 共享 artifact、PR 走查、团队审计、用量/账单可观测
- runtime verify、隔离 worktree、沙箱 runner 和 runner 池
- 面向私有部署与托管服务两种形态

---

## 历史阶段规划

按"能跑 → 能用 → 好用"三阶段推进,每个阶段必须有可演示成果。

## Phase 0:技术验证(1~2 周)

**目标**:验证核心假设"多 agent 流水线能产出可运行的简单 Web 项目"。

任务:
- [ ] One API 网关接入测试(从这个项目能调通)
- [ ] 单 Agent 跑通:用 LLM + 简单 shell 工具生成一个 Flask "hello world"
- [ ] Docker sandbox 原型:容器内执行 shell / 读写文件 / 跑测试
- [ ] **里程碑 demo**:给一句需求 "一个返回当前时间的 API",自动产出可运行代码

不做:UI、多 agent、复杂工作流。一切为验证可行性。

---

## Phase 1:最小闭环 MVP(3~4 周)

**目标**:跑通 product → architect → developer → reviewer → tester 五角色流水线,产出**一个 TodoList 全栈应用**。

任务:
- [ ] Orchestrator 工作流引擎(状态机 + Postgres 持久化)
- [ ] 五个 Agent 的 system prompt + 输出契约实现
- [ ] Sandbox JSON-RPC 工具层(fs/shell/git/browser)
- [ ] Web 控制台 v0:需求输入框 + 流程进度 + 日志流
- [ ] 人工介入:SPEC 确认页、最终合并按钮
- [ ] **里程碑 demo**:输入"做一个支持登录的 TodoList,Next.js + FastAPI",全自动产出可部署项目

成功标准:
- 80% 的 task 不需要人工干预自动通过 review
- 端到端跑完 < 30 分钟
- token 成本 < $5/项目

---

## Phase 2:工程化与迭代闭环(4~6 周)

**目标**:支持**已有项目的增量开发**,不只是从零生成。

任务:
- [ ] 代码仓库索引(embedding + 文件树),让 Agent 能"读懂"现有代码
- [ ] 增量任务模式:输入"给已有项目加个搜索功能",Agent 在现有代码基础上改
- [ ] Review→Modify 循环上限 + 自动升级到人工
- [ ] 失败重试 / 任务回滚 / 部分提交
- [ ] 项目模板库(Next.js / FastAPI / Django / Express 等常见栈)
- [ ] 成本/性能监控仪表盘
- [ ] **里程碑 demo**:fork 一个真实 GitHub 项目,提一个 issue,框架自动产出 PR

---

## Phase 3:可信度与多项目并行(后续)

- [ ] 多项目并行调度
- [ ] Agent 性能评估基准(自动化测试 agent 输出质量)
- [ ] 自动学习:从失败案例提炼 few-shot 例子注入后续 prompt
- [ ] 插件系统:允许用户自定义 agent / 工作流
- [ ] 私有部署文档,作为产品形态(可选商业化)

---

## 不做的事(明确边界)

为了不失焦,以下事项**第一年内不做**:

- ❌ 框架自我修改代码(自我进化级别的自动化太不稳定)
- ❌ 通用 agent 框架(只服务"全栈 Web 开发"这一垂直场景)
- ❌ 自己训模型 / fine-tune(全部用通用模型 + prompt 工程)
- ❌ 移动端 / 桌面端项目生成支持（这里指不把 VortoCode 扩成通用桌面应用生成器；VortoCode 自身的 Desktop 客户端属于产品入口）
- ❌ 替代人类架构师做核心系统设计(只做 CRUD 级 Web)

---

## 优先级原则

遇到取舍时按以下顺序:

1. **稳定性 > 速度**:宁可慢也不要跑飞
2. **可观测 > 自动化**:每一步都要能看到 / 能回滚
3. **范围聚焦 > 功能丰富**:只做全栈 Web,做透
4. **人工兜底 > 强行自动**:不确定就升级到人,不要硬猜
