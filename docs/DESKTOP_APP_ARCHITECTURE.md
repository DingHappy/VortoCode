# Desktop App.tsx 拆分架构方案（F-3）

> 状态：设计定稿（2026-07-19，Fable 5）。依据：对 `desktop/src/App.tsx`（5990 行）的全量结构盘点
> （128 useState / 20 useRef / 18 useMemo / 15 useEffect / 单一 300 行协议事件 switch /
> ~24 个 refresh* / ~90 个 handler / App.css 7840 行无分区）。
> 本文是委派任何 App.tsx 机械抽取的前置；抽取工单只引用本文，不自行做架构判断。

## 设计决定（先读）

1. **不引入状态库。** 128 个 state 里绝大多数是 Gateway 权威快照的镜像 + UI 开关，问题是
   **无归属**，不是缺 store。用 React 自带的 Context + 域 hook + reducer 解决；不加
   zustand/redux 依赖（引入迁移风险为零收益）。
2. **服务端权威原则不动摇**（DESKTOP_PRODUCT_SPEC 产品原则 7）：域 hook 只镜像 Gateway 快照
   与本地 UI 态，**不得在前端推导业务状态**。拆分是搬家，不是重构语义。
3. **行为冻结**：每一步 PR 不改任何视觉/文案/时序；发现 bug 记 BACKLOG 单独修，不夹带。
4. **事件总线替代大 switch**：GatewayProvider 持有连接与一个类型化分发器
   （`Map<eventType, Set<handler>>`），各域 hook 用 `useGatewayEvent("agent_stream", fn)` 订阅。
   对话域的核心状态（messages/streaming/activities/plan/promptQueue/pendingConfirm）收敛为
   **一个纯 reducer**——协议事件序列 → state 的纯函数，可以在 vitest 里无 DOM 断言，
   这是整个拆分最大的可测性收益。
5. **轮询模式统一**：现有 6 个 `connection==="connected"` 门控的 setInterval（900ms~30s）抽成
   `useConnectedInterval(ms, fn, {scope})` 一个 hook，行为逐一对齐现状（间隔、门控条件不变）。

## 目标目录结构

```text
desktop/src/
  app/          App.tsx（布局壳：topbar/banner/sidebar/conversation/inspector/settings 装配，目标 <400 行）
                AppProviders.tsx
  connection/   GatewayProvider.tsx（client、connection/connectionNote、activeScope、事件分发器）
                useRuntimeSupervisor.ts（processStatus/runtimeProcesses/recoveries，2s 监督）
                useProjects.ts（项目注册表与切换）
  session/      useSessions.ts（会话列表、activeSid 与 localStorage 持久化）
                useConversation.ts（对话 reducer + 订阅）＋ ConversationPane.tsx
                （子组件：Composer / PromptQueue / ConfirmCard / WorkspaceRequestCard）
  gitreview/    useGitReview.ts（gitReview/taskBranchReview/diff/评论/PR 交付、git_review_changed）
                GitReviewPanel.tsx
  runs/         useRuns.ts ＋ RunsPanel.tsx（TerminalPane/TestResultTree/DiffViewer 已独立，平移引用）
  tasks/        useTasks.ts ＋ TasksPanel.tsx（focusedTaskId/worktreeWorkspace 归此域）
  goals/        useGoals.ts ＋ GoalsPanel.tsx（8 个表单 state 收编为 GoalForm 本地 state）
  inbox/        useRuntimeInboxes.ts ＋ InboxPanel.tsx
  decisions/    useDecisions.ts（决策+审计，4s）＋ DecisionsPanel.tsx
  journal/      useJournal.ts（面板归属按现状 JSX 不动）
  workspace/    useWorkspaceFiles.ts ＋ FilesPanel.tsx（编辑器态；contextItems 不在此——归对话域，
                files 面板只发「添加上下文」动作）
  assets/       useProjectAssets.ts ＋ ProjectPanel.tsx（repoMemory/artifacts）
  settings/     SettingsModal.tsx ＋ useLlmProfile.ts（Tauri invoke 封装）
  notifications/useSystemNotifications.ts（系统通知投递 + notifiedDecisionIds 持久化）
  ui/           BannerProvider.tsx ＋ 既有纯组件平移（MarkdownMessage/TurnTimeline/DiffViewer/TestResultTree）
```

## 十大耦合点的归属裁决

| 耦合点（盘点 §7） | 裁决 |
| --- | --- |
| `banner`（写点 ×124） | BannerProvider 提供 `notify(tone, text)`；各域抽取时顺手迁移本域调用点 |
| `connection` / `connectionNote` | GatewayProvider 独有；只读暴露 |
| `activeScope`（引用 ×46） | GatewayProvider 派生并暴露（仍由 runtime/processStatus/repoRoot 算出，算法不变） |
| `activeSid`（×26） | session 域独有；localStorage 持久化随迁 |
| `busy`（×27） | 对话 reducer 独有；其他面板经 context 只读 |
| `processStatus`/`runtimeProcesses` | connection 域独有；sidebar/inbox/recovery 只读 |
| **taint 门控**（confirm 卡样式、审查/PR 闸门） | 渲染归 ConfirmCard 与 GitReviewPanel；**vitest 固定断言
  「tainted 确认必渲染污点样式且无法被 props 关闭」**——把 V0 验收「污点提示不可被隐藏」变成代码保障 |
| `gitReview`+`taskBranchReview` 合并 | useGitReview 内部 selector（`displayed = taskBranch ?? git`），面板只读合并结果 |
| 对话四件套（messages/streaming/activities/plan） | 同一 reducer 的字段；`clearSessionView` 变为 reducer 的 `RESET` action |
| `contextItems`（composer 与 files 面板共用） | 对话域独有；files 面板经 action 添加 |

## 迁移顺序（绞杀者模式，每步一个 PR、check 绿、行为冻结）

- **P-1 地基**（架构关键，Fable 做或紧审）：BannerProvider + GatewayProvider（大 switch 先整体
  平移进 provider，不拆）+ `useConnectedInterval`。App.tsx 其余不动，只从 context 取 client/connection。
- **P-2 叶子面板**（可委派，每面板一单）：Goals → Inbox → Decisions → Project/assets。
  每单 = 面板 JSX + 本域 state/refresh* 迁入域 hook + 本域 banner 调用点迁移 + 本域 css 搬家。
- **P-3 中等耦合**（可委派）：Runs → Tasks → Files/编辑器（注意 contextItems 留在对话域）。
- **P-4 对话 reducer**（行为风险最高，Fable 做或紧审）：大 switch 拆成纯 reducer + useConversation；
  vitest 覆盖事件序列→state（至少：stream 增量、done/cancelled 清理、confirm/tainted、queue 变更、
  history 水合）。放在面板都薄了之后做。
- **P-5 Git 审查域**（最大面板，可委派 + Fable 复审）：GitReviewPanel + useGitReview + PR 交付。
- **P-6 收尾**：App.tsx 收敛为布局壳（<400 行）；App.css 剩余壳层样式归位（css 与各面板 PR 同步
  搬家，class 名不变）。

## 每步验收（写进抽取工单）

1. `npm run check` 绿（tsc + cargo test）；vitest 绿（B6-3 脚手架为前置）。
2. 新迁移的域 hook/reducer 至少有 1 个 vitest 文件；P-4 的 reducer 测试为强制项。
3. `git diff --stat` 中 App.tsx 行数单调下降；不新增任何行为分支。
4. 污点渲染断言（见耦合点表）自 P-1 起进测试套件，此后每步保持绿。
5. 现有 bundle smoke（React/IPC/sidecar）不回归。

## 排期挂载

- 前置：B6-1（落库）+ B6-3（vitest 脚手架）合并。
- P-1 进 B7 批；P-2 起逐单进 BACKLOG（B7/B8），每单独立 vorto/* 分支 + PR。
- 本文随 B6-1 第 8 层提交入库；行号引用以当日盘点为准，抽取时以 class/函数名定位，不依赖行号。

## 2026-10-03 任务域迁移进度

`hooks/useTasks.ts` 接管任务列表、选中任务、工作区快照与读写操作；
`tasks/taskFeed.ts` 是每 hook 实例的服务器数据镜像，通过 React useSyncExternalStore
订阅，不增加状态库或全局 store。协议仍由 App 唯一分发，通知和导航仍由 App 装配。

本次包含一个单独明确的行为修正：在途 HTTP 列表不能覆盖后来收到的任务事件；
多个 HTTP 读取只接纳最新请求；连接替换或项目视图重置后拒绝迟到的读写结果。
恢复结果按服务器 task ID 合并，避免同 ID 重复行。项目重置同时清空选中任务。
这些边界由 `tasks/taskFeed.test.ts` 的异步受控请求验证；完整 TasksPanel 和 GatewayProvider
尚未迁移，不把本次记为 S1 全部完成。原生 UI 与真实模型仍需独立验收。

### 任务面板与握手适配后续

`tasks/TasksPanel.tsx` 已承接任务入口、worktree/计划列表、任务卡装配和定位滚动；
App 仅注入快照、下派模型和领域动作，原 DOM 层次、class 与文案保持一致。
`connection/sessionConnection.ts` 使用同一 clientRef 管理握手/断开代次，旧握手、旧连接事件、
迟到失败和断开结果不能推进新连接的 UI 状态。项目注册、runtime 启动、跨域刷新和通知
编排仍在 App；这不是完整 GatewayProvider 迁移。

新增真实组件静态渲染测试验证离线下派门、委派待验收与过期验证的 PR 门，Vitest 入口
包含 `.test.tsx`。并发连接测试通过受控 Promise 验证，未依赖真实 socket 或模型服务。

### 开发恢复与任务定位

开发恢复由 gateway/task_recovery.py 校验一次恢复身份和来源/计划摘要，使用原 TaskRunner；
HTTP 层只适配状态与错误，不增加调度器。来源任务通过 task_update 刷新 can_resume 和
resumed_task_id；同一恢复请求返回 replayed 及原子任务实际状态。useTasks 按返回 ID
合并并定位恢复任务，重试提示已有恢复，仍使用原连接代次拒绝迟到响应。
dev-resume 的完成交接复用 TaskHandoffActions，有独立结果版本与回执。有效恢复血缘
仅消除旧来源的重复注意事项计数，不自动确认其历史交接。

## 2026-10-04 依赖任务入口

TaskDispatchForm/useTaskDispatch 接收所属会话的服务器任务快照，选择前置研究任务并固定
ID/轮次；提交仍走 GatewayClient.submitDelegation 的请求幂等合同。切换会话清空选择，
迟到响应仍由原连接身份隔离，刷新后的轮次变化要求重新选择，不静默修改请求合同。
TaskDependencyActions 只显示服务器的 ready/failure/consumed 投影，以共用 useTaskAction
控制单次提交与连接隔离，releaseTaskDependencies 使用原 HTTP 服务，界面不调度 Agent。
失败后提示新建合同，保留原任务的失败证据；空依赖字段兼容旧研究卡的验收/返工。
依赖核对集中在 gateway/task_dependencies.py，推进与排队使用原 DispatchService 和
TaskRunner。主 Agent/HTTP/任务卡不增加各自状态机或执行池；范围与运行证据见
TASK_COLLABORATION_PRD.md 第 25 节及 DESKTOP_ACCEPTANCE.md。

## 2026-10-04 任务链额度入口

TaskDispatchForm 可为新的研究根任务启用 capabilities.chain_budget.defaults 中的总额度。
useTaskDispatch 将 chain_limits 纳入原请求身份；提交响应丢失时保留同一请求合同重试。
选中带额度的前置后，控件显示继承并禁用重设；继承提交不发送 chain_limits。前置轮次、
归属、损坏投影及不同额度根在提交前校验，服务端仍为最终权威。

TaskChainBudget 只显示服务端重新计算的任务数、轮次、步骤和超时配额及损坏原因，
不在客户端扣款或退款。TaskFeed 收到带额度的任务事件后重读权威快照，更新同链
其他卡片，仍用原请求代次和连接隔离拒绝迟到读取。额度不足时原任务动作表单保留
草稿，等待状态不被界面推进；
取消与早结束也不改变根的历史预留。所有入口复用 gateway/task_chain_budget.py、
TaskLedger、原共享锁和 TaskRunner，未增加连接、调度器或自动模型回合。
步骤和超时按每轮原合同预留，不声称 token/费用硬上限；验收证据见
DESKTOP_ACCEPTANCE.md，阶段范围见 TASK_COLLABORATION_PRD.md 第 26 节。

## 2026-10-04 依赖失效与停止入口

TaskDependencyActions 消费服务端 result_valid/can_reconcile/stop_pending 投影，提供核对
并记录失效或核对失效并停止。请求通过 GatewayClient.reconcileTaskDependencies，固定
任务 owner 与当前轮次；复用 useTaskAction 的重试和连接隔离。停止请求保存后显示
清理等待，明确要求以最新任务状态确认完成，界面不直接改写执行状态。

TaskCard 保留历史结果和审查，单独标明依赖失效；直接验收/返工、主 Agent 交互草稿和
问题答复入口在失效投影下拒绝使用。交接摘要与主 Agent 使用相同失效提示，新的失效
revision 使旧处理回执不再覆盖本次通知。核对失败保留可重试入口；后台实际收尾经原
任务更新和快照刷新显示，没有新增本地调度器或自动模型回合。范围见 PRD 第 27 节。

## 2026-10-04 任务事件刷新补漏

TaskFeed 收到服务研究的 task_update/task_handoff 后重读权威快照，包含没有 chain_budget
的前置变化。由服务端重新投影 waiting.ready 和 consumed.result_valid，客户端不遍历
依赖图或改写验收/执行状态；中间前置不在本地列表时也触发读取。

同一连接/重置代次的事件读取只保留一个在途请求和一个待刷新标记；同一微任务内的
update/handoff 合并。读取期间任何新 upsert 都标记补读，因此无关任务事件导致原响应
被版本门拒绝后，兄弟额度/下游投影不会永久漏刷新。新完整快照取消待补读；重置和
连接切换拒绝旧响应并丢弃旧队列。读取失败保留现有快照，等待新事件或手动刷新重试，
不自发轮询。手动读取继续使用原请求序号隔离；本合同只合并事件驱动的请求。

受控异步请求和真实任务卡/WebSocket/HTTP 的本地验收见 DESKTOP_ACCEPTANCE.md；
该切片不创建反向索引，不自动持久记录下游失效或停止任务。范围见 PRD 第 28 节。

## 2026-10-04 服务端下游协调

反向发现与停止集中在 gateway/task_dependents.py；DispatchService 的协作通知、主 Agent
审查适配和 TaskRunner 更新调用同一有界领域路径。启动恢复后和已有所属用户回合可补查
离线变更，核对不会启动付费模型。没有消费合同的用户回合先只读判断，不构造后台池；
工作区不一致时保留核对不完整提示，不借另一工作区的池修改当前记录。

Desktop 继续消费原任务事件和服务端失效投影，保留 TaskFeed 的事件合并与连接隔离。
历史 done/result/review 不改写；失效 marker 改变交接版本，现有任务卡显示新的未处理
通知。类型能力报告补充服务端发现/核对标记及限额，本切片没有新增客户端依赖状态机、
批量动作控件或执行池。公开 GET dependents/POST reconcile-dependents 可供服务调用方
使用，单任务 UI 仍走原 reconcile 入口。范围和验证见 PRD 第 29 节及 DESKTOP_ACCEPTANCE.md。

## 2026-10-04 大台账分页呈现

TaskCoverageActions 在 API 服务任务卡中提供开始、显式下一页、恢复上次核对、检查有效性
和重新开始。GatewayClient 的 scanTaskDependencies/taskDependencyScanStatus 调用同一
服务端领域合同；UI 不构建依赖图，不自动分页、推进任务或调用模型。服务端保留覆盖
检查点，浏览器重载后通过状态查询恢复原 request_id/游标，而不是猜测读到第几页。

记录读取尚未结束时显示“下游范围尚未完整确认”，避免把 0/0 当成不存在下游；记录
读完但尚未核对下游时仍显示覆盖未完成。完成只显示“上次覆盖核对已完成”与观察时间，
检查/继续时服务端重新验证快照。变化、损坏或响应失配显示 unknown；网络失败保留
原游标重试，旧完成记录不遮盖本次未知状态。停止与历史验收/交接处理仍使用原任务卡。

useTaskAction 的重复动作模式复用单一在途与连接隔离，并向回调提供 isCurrent；结果
必须在提交局部状态前确认当前连接。taskCoverage.ts 校验来源/轮次/请求、摘要格式、
累计计数、图/操作范围及完整条件；继续不能换到另一个快照或跨越多个未确认序号。
原单次回答、验收和回执表单不改变成功后禁用行为。实际客户端与冻结 runtime 验证见
DESKTOP_ACCEPTANCE.md，范围见 PRD 第 30 节。

覆盖资料管理仍放在原 TaskCoverageActions 的 TaskCoverageRecords 组件中，复用同一个
useTaskAction 请求互斥与连接提交门。显式加载容量及固定来源资料，保留归档后重新读
权威容量；释放绑定已核验归档和原活动版本，成功后清除对应活动报告并重新读容量。
历史资料只在独立标记区域展示，不能注入活动覆盖或恢复游标。导出调用真实公共 HTTP
接口，仅在响应仍属于原连接与固定归属时触发下载；下载不作为服务端释放准入条件。
档案页导航只浏览有界历史资料，不累计任务图覆盖。合同见 PRD 第 31 节。
