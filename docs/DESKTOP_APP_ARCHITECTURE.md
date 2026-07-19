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
