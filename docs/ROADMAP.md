# 开发路线图

> 2026-07 更新：早期阶段规划保留为历史参考；当前产品方向是把同一个 VortoCode agent runtime
> 拆成 **CLI 版、Desktop 版、Web 服务版** 三种入口，避免三套实现各自分叉。

## 产品版本路线

### 当前产品优先级：Desktop-first

VortoCode 先把 **Desktop 作为默认产品、安装入口和主要体验面**。它比终端入口门槛低，也最适合承载
多项目常驻、后台 Agent、人工确认、diff、预览、Goal 证据和系统通知。CLI 不退役，但定位收敛为高级用户、
脚本自动化、远程终端和 runtime 调试入口；Web 服务版继续共享同一内核，当前不与 Desktop 争抢界面开发资源。

### Desktop 版（本地常驻工作台）

定位：默认面向用户交付的本地优先 Agent 产品，而不只是 CLI 的可视化外壳。

当前状态：V0 协议客户端和真实 runtime 握手已落地；V1 已具备 Git scope 源码预览、最多 500 行的
结构化上下文选择、VS Code/Cursor/系统默认编辑器跳转，以及带 diff、人工确认、SHA 冲突检测和
原子替换的内嵌源码保存闭环；同时具备结构化运行/预览、Git hunk 审查、Goal 自动验收、Worktree
暂停恢复/交接和 PR/CI 失败修复入口。
当前还已接入共享脱敏审计时间线、聚合人工确认/阻塞/PR 反馈的决策中心，以及从 Goal 证据、任务交接和运行台账恢复的 Project Journal；Journal 已提供跨日周报、安全的“继续昨天”和显式授权的系统通知。
Project Control Center 第二阶段的 Runtime Supervisor 已落地：Tauri 原生最近项目注册表、Git 顶层目录归一化、按项目会话恢复，以及在未保存编辑和托管 runtime 边界上的受保护切换。Desktop 启动自动进入 General 无目录会话，普通任务直接发送；只有 Agent 结构化请求文件能力时才由用户审阅并切到按会话隔离的 Scratch 或明确选择的 Git Project。General 的 Agent 工具和 REST 面都禁止文件、Shell、Git、仓库记忆与开发任务，且范围切换不复制旧历史。每个 General、Scratch 和 Project 都有独立 runtime 身份、回环端口、日志和不含 token 的恢复记录；项目切换只移动前台 WebSocket，执行中的 session actor 和后台任务留在原 runtime 继续运行，切回后按持久 cursor 补收事件。侧栏汇总所有受托管 runtime 的健康状态，异常退出按项目标记并可安全恢复；旧单条恢复文件会自动迁移成多 runtime 注册表。首选端口冲突时自动回退空闲端口，最多同时托管 12 个 runtime，正常退出仍回收所有由 Desktop 启动的进程组。按平台冻结的 core Gateway sidecar、固定并校验的 standalone CPython 来源、macOS 开发者预览 `.app`/`.dmg` 及真实 bundle GUI smoke 已落地。
跨项目 Inbox 已建立在每个 Gateway 的只读权威快照上：Desktop 单次轮询即可聚合各 runtime 的确认、Hook、会话、Goal 与后台任务，单项目失联独立降级，凭据只保存在进程内存；用户可从全局事项切回对应项目、会话或工作台。
产品边界与里程碑见
[Desktop 产品规格](./DESKTOP_PRODUCT_SPEC.md)。

- [x] 一等 Goal 合同：目标/约束/非目标/验收标准、任务与 DevPlan 关联、证据完成闸门
- [x] Desktop Goal 面板：草稿编辑与确认执行、查看阻塞与进度、逐项验收、采用自动证据、失败后新一轮执行或断点续跑
- [x] 终端/测试/预览：结构化运行记录、沙箱证据、退出码、停止/重跑与本地 Browser Preview
- [x] Git 审查：working/staged、hunk 级操作、行级评论修复、reviewed commit 与 Draft PR
- [x] Goal 自动验收器：test/build/lint/file、超时、隔离执行与证据自动回写
- [x] Worktree 会话：实时 worktree、DevPlan 块进度、任务暂停/恢复、血缘与交接摘要
- [x] PR/CI 反馈：Review、checks、失败日志定位与一键交给 Agent 修复
- [x] 决策与审计中心：实时确认、Goal/任务/运行阻塞、PR 反馈聚合；工具参数脱敏、权限允许/拒绝留痕
- [x] Project Journal：今日摘要、7 天周报、下一步、任务交接、Goal 证据、完整时间线、去重快照与历史恢复
- [x] Desktop 系统通知：用户显式授权、首次同步只建基线、仅投递新增高优先级决策且不携带敏感正文
- [x] Desktop 运行中输入队列：服务端权威 FIFO、删除、立即执行、停止后保留与跨重启恢复
- [x] Agent Dashboard 第一阶段：磁盘与 live 会话合并、等待确认/执行中/暂停队列/空闲/未加载状态、按可操作性排序
- [x] Agent Dashboard 上下文增强：Gateway 权威提供 cwd、branch、HEAD、主/关联 worktree、会话归属后台任务与上下文窗口占用；失败/中断任务提升为待处理状态
- [x] 后台任务稳定关联：有界生成态账本连接 task、owner session、DevPlan 与实时 worktree；Desktop 会话状态、计划和 worktree 均可直达对应交接卡
- [x] 任务分支审查：任务交接直达持久 `vorto/*` diff；运行中 worktree 只读，成功任务支持稳定 hunk 接受/撤销，撤销后必须隔离重验才可开 Draft PR
- [x] 可选团队审查策略：`.vortocode/review-policy.yaml` 可要求全部当前文本 hunk 完成精确接受；Desktop 显示覆盖率，PR 服务端实时重算并对坏配置 fail closed
- [x] Session actor 第一阶段：回合不再归属于单条 WebSocket，刷新/关窗继续运行，同 sid 多客户端附着并补收 hydrate 竞态窗口事件
- [x] 持久事件回放：协议 v8 单调 `seq`、分段 append-only JSONL、按 sid 保存 Desktop cursor、`after_seq` 有界补收与日志轮转
- [x] 后台任务会话交接：任务台账持久化 `owner_session`，终态通过 `task_handoff` 唤醒所属会话并支持断线回放
- [x] 项目 Hook 信任闸门：信任状态保存在仓库外，Desktop 显式预览/授权/撤销并热重载；Hook 超时和崩溃 fail-open，坏 matcher 不扩大范围
- [x] Hook 能力收敛：调用级最小能力、仓库 allowlist 只减不增、独立深拷贝事件；被动 Hook 不再修改主循环或注入模型工具结果
- [x] Hook 可观察性与副作用归因：协议 v8 发送开始/完成/超时/阻止事件，Desktop 回合时间线可展开结果；Agent/Hook 命令文本改动进入仓库外来源账本
- [x] Hook 可操作聚合：Dashboard/决策中心汇总失败、超时与阻止事项，定位原会话并以用户自有状态确认查看
- [x] Git 审查实时失效：协议 v9 推送 HEAD、index、文件与来源变化，Desktop 自动刷新权威 diff 且不产生只读刷新自循环
- [x] Project Control Center 第一阶段：原生最近项目、按项目会话、Git 工作区校验与安全切换
- [x] Runtime Supervisor 第一阶段：多项目/多 Scratch 常驻、精确启动复用与停止、项目级恢复/日志、后台状态和切回续接
- [x] Desktop 跨项目 Inbox：Gateway 权威有界快照、失败隔离、内存凭据、全局通知去重与项目/会话/Goal/任务跳转
- [x] Desktop 模型服务快捷配置：VortoCode Relay、自定义 OpenAI 兼容与本机服务预设，macOS Keychain 保存，当前 runtime 安全重启生效
- [x] General / Scratch / Project 会话范围：默认无目录、结构化升级请求、隔离临时目录与 Agent/REST 双层权限边界
- [x] 项目资产工作台第一阶段：仓库记忆安全投影/确认写入、Artifact 列表、版本选择、静态隔离预览与 Agent 迭代入口
- [x] 多 runtime 崩溃恢复、macOS `.app`/`.dmg` 开发者预览打包与真实 bundle GUI smoke
- [x] 按 target triple 构建的内置 core Gateway sidecar、固定启动边界、health/Web 资源与 bundle 内启动验收
- [x] Desktop 可审计供应链第一阶段：双架构固定 standalone CPython 来源/哈希/运行时树、独立锁定环境、实际冻结组件与 CPython runtime notices、产物哈希及 preview/release 证据闸门

- Runtime Supervisor 后续：后台 CPU/并发资源预算、逐项目模型配置覆盖与跨项目暂停/恢复控制
- Runtime 与 Desktop 主线后续：多 runtime 常驻与跨项目 Inbox 已落地；下一步做资源调度和后台任务优先级。Hook capability 收敛、可选“全部 hunk 已决策”团队策略、失败聚合、Git baseline 实时推送、Dashboard 上下文与任务分支审查均已落地。
- 正式分发：notices 人工复核、Developer ID 签名、公证、升级通道，以及 Apple Silicon 原生 arm64 与后续 Windows/Linux 平台产物验收
- 项目资产的跨项目聚合、带 token 的安全浏览器交互预览与更完整的后台通知中心
- 本机工作区和用户权限仍是边界，不默认上传代码

### CLI 版（高级与自动化入口）

定位：为终端重度用户、脚本、CI、远程主机和 runtime 调试保留最快的本机入口。

- `vc tui` / `vc agent` / `vc server` 与 Desktop 共享同一 agent、权限、审计和数据合同
- plan/build 权限门、写盘确认、工具审计、token/context 使用量保持一等能力
- 保留 session resume、slash commands、管道化调用和无图形环境运行能力
- CLI 的新交互功能以服务 Desktop 内核复用或自动化场景为准，不再单独抢占产品界面优先级

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
