# VortoCode Desktop 产品规格

> 状态：Desktop-first；V1 已形成运行、Git 审查、Goal 验收、Worktree、PR/CI、决策审计、跨日 Journal、系统通知、可恢复输入队列、多 runtime 常驻与跨项目 Inbox 闭环 · 2026-07-17

## 一句话定位

VortoCode Desktop 是面向开发者的本地优先 Agent 工作台：在一个窗口里管理项目、会话、
开发目标、隔离 worktree、后台任务、人工决策和交付结果。它不是另一套 agent runtime，也不是把 TUI
简单包进 WebView。

Desktop 是 VortoCode 当前默认产品与首要分发面；CLI 是高级/自动化入口，Web 是未来团队与托管入口。
三者继续共享一个内核，但新用户主链路、交互质量和跨项目工作首先在 Desktop 验收。

## 产品原则

1. **一个 runtime，多种客户端**：Desktop 只消费 `vc server` 的冻结协议；agent、权限、污点、
   沙箱和记忆规则继续由 Python runtime 统一执行。
2. **人在决策口，不在人肉搬运口**：把需要批准、需要 review、CI 失败和任务阻塞聚合成决策队列；
   运行日志默认折叠，按需展开。
3. **本地优先**：默认仅连接 `127.0.0.1`；代码、会话和记忆留在本机。远程连接必须使用 TLS 和 token，
   放到后续里程碑单独设计。
4. **开发任务优先**：先把会话、diff、终端、预览、PR/CI 做深；源码查看与轻量编辑逐步加入，
   但不在 V0 扩成通用电脑管家或完整 IDE。
5. **隔离优先**：并行开发以 worktree/后台任务为边界，不让多个会话抢写同一个工作区。
6. **目标完成必须有证据**：后台任务返回只代表一次执行结束；每条验收标准都有通过证据后，
   Goal 才能进入 `achieved`，失败证据则形成可继续执行的 blocker。
7. **Desktop 是控制面**：项目切走不等于任务停止；跨项目 Inbox 只聚合各 Gateway 的权威有界快照，
   不在 React 里重新推导业务状态，也不把项目 token 持久化到浏览器存储。
8. **模型服务可替换**：VortoCode Relay 是快捷默认值而不是产品锁定；用户可在 Desktop 内切换任意
   OpenAI 兼容 HTTPS 网关或本机回环服务。LLM Key 只进入 macOS Keychain 与子 runtime 环境。

## 竞品借鉴矩阵

| 产品 | 借鉴内容 | VortoCode 的落点 |
| --- | --- | --- |
| Codex App | app-server、并行任务台、worktree、集中 diff、自动任务 | 复用 gateway 协议；项目/会话/任务三层导航 |
| Claude Desktop | 可拖拽多面板、自动 worktree、side chat、Browser Preview、PR/CI | 对话主区 + Diff/任务/通知检查器；结构化终端、预览与 PR/CI 已进入检查器 |
| 腾讯 Marvis | 常驻、本地隐私、手机接管、系统与文件入口 | 本机 runtime 生命周期；后续做远程伴侣与本地语义搜索 |
| Marvis Company Brain | Journal、To-dos、Tasks、Project、Universe | 决策队列、每日摘要、项目上下文；知识图谱不进 V0 |
| xAI grok-build | 服务端权威 Prompt Queue、后台/前台切换、会话事件持久化、稳定 hunk 身份 | 队列、会话交接、持久事件与稳定 hunk/source 第一阶段均已落地 |
| VortoCode | 隔离 dev 流水线、国产模型 Relay、权限审计、checkpoint/rewind | 作为差异化能力直接显性化，不复制竞品的模型绑定 |

参考：

- <https://learn.chatgpt.com/docs/app-server.md>
- <https://code.claude.com/docs/en/desktop>
- <https://marvis.qq.com/>
- <https://justaskmarvis.com/>
- <https://github.com/xai-org/grok-build>

第三方项目只用于公开能力对照；任何源码复用都必须在对应变更中固定来源版本、核对许可证，并登记到第三方 notices。

## 信息架构

```text
┌ 项目 / 全局 Inbox ┬ 对话 / 计划 / 活动时间线 ─────────┬ 检查器 ──────────┐
│ 项目与 runtime    │ 当前会话、流式回复、工具摘要        │ Diff             │
│ 跨项目待处理/运行  │ 输入框、排队输入、Plan/Build、停止   │ Goal / 后台任务   │
│ 新建/恢复/重命名  │ 人工确认横幅                        │ 通知/待决策       │
└──────────────────┴───────────────────────────────────┴──────────────────┘
```

顶部状态栏固定展示：工作区就绪状态、项目路径、模型、协议版本和当前权限模式；runtime 地址和 token 只在高级连接设置中展示。

## V0 范围

### 必须完成

- Tauri 2 + React + TypeScript 可构建工程。
- Desktop 总是先进入主工作台并自动连接 General 无目录 runtime，不把目录选择作为启动或首条消息门槛。普通对话、研究、规划和 Artifact 直接运行；只有任务确实需要文件时，Agent 才通过 `workspace_required` 请求 Scratch 或 Git 项目。范围切换后由 Desktop 自动启动本机 `vc server`、等待 health 并连接协议；进程固定监听 `127.0.0.1`，首选端口被占用时自动选择空闲端口。
- 会话 scope 是权限边界而不是路径标签：General 不提供文件、Shell、Git、仓库记忆或开发工具；Scratch 使用 app-data 下按会话隔离并初始化 Git 的目录；Project 只接受用户明确选择并归一化的 Git 根目录。不得回退到 HOME、最近项目或内部持久化目录。
- General 到 Scratch/Project 是新的信任域与新 runtime 会话，不复制对话历史。`workspace_required.task` 必须展示给用户审阅，只回填新会话输入框，不自动执行。
- 使用官方 Tauri HTTP/WebSocket 插件连接 gateway；支持 Authorization header。
- 校验 `init.v` 协议版本，展示 runtime 的 workdir/model。
- 多会话列表、新建、切换、恢复、重命名、删除。
- `agent_history`、`agent_plan`、`agent_stream`、`agent_emit`、`agent_say` 的可视化。
- `agent_confirm` 人工允许/拒绝；污点状态必须显式展示。
- `agent_diff` 结构化 diff 着色。
- 后台任务快照/增量、通知台账、取消任务、成功任务开 draft PR。
- 默认本机连接；不修改 Web 服务现有 CORS 或鉴权边界。

### 明确不做

- V0 不内嵌 Monaco/VS Code；这不是永久排除编辑能力，而是先稳定 Agent 主链路。
- 不在 Desktop 再实现权限、污点或工具策略。
- 不做任意 shell 启动器；runtime 启动命令固定为 `vc server`，参数受白名单校验。
- 不做任意远程 `ws://`；远程形态需要 `wss://`、配对与凭据存储设计。
- 不做系统级 Computer Use、手机伴侣和知识图谱。

## 协议映射

| UI | 入站 | 出站 |
| --- | --- | --- |
| 连接状态 | `ping`, `get_status(hydrate=true)` | `init`, `pong`, `status(v)` |
| 对话 | `agent(context_files, context_selections)`, `agent_cancel`, `agent_queue_remove`, `agent_queue_send_now` | `agent_history`, `agent_stream`, `agent_emit`, `agent_done`, `agent_queue(items, running)` |
| 计划与活动 | — | `agent_plan`, `agent_say`, `agent_reasoning` |
| 人工确认 | `agent_confirm_response` | `agent_confirm` |
| Diff | — | `agent_diff` |
| 源码保存 | `workspace_edit(path, content, expected_sha256)` | `agent_diff`, `agent_confirm`, `workspace_edit_result` |
| 后台任务 | `task_list` + REST | `task_snapshot`, `task_update` |
| Goal 合同 | REST：创建、执行、逐项证据 | 复用关联任务的 `task_update` 后刷新 Goal |
| 自动验收 | REST：配置 verifier、运行验收 | test/build/lint/file 形成结构化 Run 与 Goal 证据 |
| Git 审查与交付 | REST：hunk 操作、commit、Draft PR、PR/CI 状态与失败日志 | `git_review_changed` 触发权威 diff 重取；外向操作保留显式确认，CI 日志按不可信数据处理 |
| Worktree 任务 | REST：任务暂停/恢复、worktree/plan 快照 | 展示计划块、任务血缘与交接摘要 |
| 决策与审计 | REST：`/api/decisions`、`/api/audit` | WS 确认仍走冻结协议；工具参数默认脱敏，不持久化结果正文 |
| 跨项目 Inbox | REST：每个本机 Gateway 的 `/api/runtime-inbox` | Desktop 每 4 秒并行读取一次有界快照；失联按 runtime 降级，点击后切到权威项目/会话处理 |
| 模型服务配置 | Tauri 原生命令 + macOS Keychain | Relay/自定义/本机预设；保存时校验 URL/模型/Key，并只重启当前 runtime 注入环境 |
| Journal | REST：读取、快照、历史日期、7 天聚合、恢复对账和手工记录 | 从审计、Goal 证据、任务交接与运行台账确定性生成；历史快照冻结，旧动作必须与当前决策队列对账 |
| 通知 | REST + Tauri notification plugin | `notice`；系统投递仅在用户显式授权后开启，正文只使用通用计数提示 |
| 项目资产 | REST：`/api/repo-memory`、`/api/artifacts`、版本与 raw | 仓库记忆读取投影先脱敏，追加必须显式确认并过 `MemoryWritePolicy`；Artifact 内置预览禁脚本/交互/外联，迭代通过 `@artifact:<id>` 回到 Agent |

协议新增必须先改 `src/gateway/protocol.py` 并补契约测试；Desktop 不靠匹配文案推断安全状态。
`hydrate=true` 用于原生 WebSocket 客户端挂好监听后幂等重取版本、历史和计划，避免握手首帧竞态；
协议 v8 为会话实时事件增加单调 `seq`，`agent_events_replay(after_seq)` 返回有界补收批次；Desktop
按 sid 保存最后 cursor。事件分段写入 `.vortocode/session_events/`，单段和段数均有上限，坏尾行
按断电恢复语义忽略；人工确认、原始 reasoning 和工作区升级请求只写 cursor 占位、不持久化正文，
避免 Gateway 重启后复活已经失效的操作请求。完整 hydrate 仍是新客户端的权威状态基线。
协议 v9 增加 `git_review_changed`：Project/Scratch 会话的 HEAD、逻辑 index、改动文件或来源账本
变化时，Desktop 自动刷新 Git snapshot/diff；该事件同样只持久 cursor，不把过期路径当作恢复事实。
不带该字段的现有 CLI/TUI/Web 行为保持不变。

## 里程碑

### V0 · 协议客户端

完成本规格“必须完成”项，能连接当前仓库的 `vc server` 跑真实会话和确认流程。

### V1 · 多任务开发台

- 已落地结构化项目运行控制台：terminal/test/preview 共享沙箱策略，持久化 PID、输出、退出码、停止/重跑与本地预览地址。
- 已落地服务端权威 Prompt Queue：Agent 运行中仍可继续输入，待运行项支持删除和“现在执行”；队列按会话持久化，普通完成后 FIFO 推进，显式停止保留队列但不擅自续跑，“现在执行”先取消当前回合再提升指定项，始终保持单会话单前台回合。
- 已落地 Session actor 与持久事件回放：刷新/关窗只卸载观察端，不取消当前回合；同 sid 可多客户端附着，协议 v8 cursor 能跨 Gateway 重启补收仍在保留窗口内的事件。
- 已落地后台任务会话交接：TaskRunner 记录发起会话，完成、失败、暂停或中断后通过持久 `task_handoff` 唤醒该会话；默认只生成交接证据与通知，不隐式启动新的付费 Agent 回合。
- 已落地项目 Hook 安全接入：Desktop 在工作区设置中预览命令/HTTP 目标并显式信任或撤销；信任记录位于仓库外，未信任仓库不能靠提交配置自行获得本机执行权，变更会热重载当前 Web/Desktop 会话。
- 已落地 Hook 调用级能力合同：Runtime 按事件与实现注入最小能力，仓库 allowlist 只能缩减；每个 Hook 使用独立深拷贝事件，被动 Hook 不能修改主循环数据或把消息注入模型工具结果，Desktop 信任卡显示有效能力。
- 已落地 Hook 可观察性：协议 v8 把每个可见 Hook 的开始、完成、失败、超时和明确阻止作为持久活动事件；Desktop 回合时间线能展开事件、关联工具、耗时和脱敏结果，内置审计 Hook 不产生界面噪音。
- 已落地 Hook 可操作聚合：失败、超时与明确阻止会提升 Dashboard 会话状态并进入决策中心，可定位原会话后标记“已查看”；确认账本属于用户应用状态，不接受仓库内数据声明，也不复制错误正文。
- 已落地 Dashboard 上下文增强：会话行由 Gateway 权威展示 cwd、Git 分支、主/关联 worktree、隔离 worktree 数量、归属后台任务和上下文窗口占用；失败、暂停或中断的后台任务提升为待处理，Desktop 不解析消息文案或自行运行 Git 猜状态。
- 已落地 Git 审查与交付：working/staged 双视图、文件与 hunk 暂存/取消暂存/撤销、行级评论驱动 Agent 修复、仅提交已审查 index，以及显式确认后创建 Draft PR。hunk 使用不含行号的内容稳定 ID，另保留精确 patch SHA-256 防止过期操作；Desktop 标注 Agent、本人、混合或外部修改，来源哈希台账位于仓库外的用户应用数据目录。
- 已落地 Git 审查实时失效：Gateway 以逻辑 index 和隐私安全元数据生成稳定 watch token，外部编辑、stage/unstage、HEAD 与来源变化通过协议 v9 唤醒 Desktop 自动重取权威 diff；只读刷新不会形成事件自循环。
- 已落地 PR/CI 反馈面：当前分支 PR、Review 决策、checks、失败 Job/Step 与日志摘录；失败证据需用户点击后才交给 Agent，并作为外部不可信数据处理。
- 已落地 Worktree 任务面：实时一次性 worktree、持久 DevPlan 块进度、暂停与断点恢复、父任务血缘和可复制交接摘要。
- 已落地稳定任务关联：后台 worker 通过执行上下文把 task、owner session 与 plan 绑定到每个实时 worktree；关系账本不保存提示词或源码。会话后台状态、计划和 worktree 均可在 Desktop 直达同一任务交接卡，刷新后仍可恢复。
- 已落地任务分支审查：任务卡可进入对应持久 `vorto/*` 分支 diff；运行中的一次性 worktree 只读，成功任务可按稳定 hunk 记录接受证据或显式撤销。撤销生成独立审查提交并立即废弃旧测试证据，只有精确新 HEAD 在隔离 worktree 重验通过后才重新开放 Draft PR。
- 已落地可选团队审查策略：仓库可在 `.vortocode/review-policy.yaml` 要求任务分支的全部当前文本 hunk 都有稳定 ID + patch SHA-256 接受证据；Desktop 显示覆盖率并关闭未满足的 PR 入口，服务端交付时重新读取当前 HEAD，配置错误或覆盖率未知均 fail closed。缺省关闭，不强迫个人工作流逐块确认。
- 已落地 Goal 自动验收器：test/build/lint 命令必须按无人值守隔离策略执行并受超时约束，file 检查限制在仓库内；所有结果自动回写对应验收标准。
- 已落地决策中心：聚合实时确认、阻塞 Goal、失败/暂停任务、失败运行和 PR Review/CI；历史本地事项可忽略，实时确认必须明确允许或拒绝。
- 已落地共享审计时间线：Web/Desktop Agent 与 TUI 写入同一 `.vortocode/audit.log`；敏感键、Bearer/环境变量凭据和文件正文默认脱敏，只记录工具结果长度。
- 已落地 Project Journal：每日摘要与 7 天周报聚合任务、Goal 证据、运行、工具和权限决定；任务交接使用 Gateway 共享投影，快照按内容摘要去重，历史日期只读冻结，手工记录自动脱敏凭据样式内容。
- 已落地安全的跨日恢复：Desktop 的“继续昨天”只把历史目标 ID 当作线索，Gateway 会与当前待决策队列重新对账；已完成、已忽略或不存在的旧动作不会被执行。
- 已落地 opt-in 系统通知：只请求检查/申请/发送通知三项 Tauri 权限；首次授权只建立当前高优先级事项基线，后续通知只显示新增数量，不把命令、日志、PR 评论或确认正文带到系统锁屏。
- 已落地 Project Control Center 第二阶段 Runtime Supervisor：最近项目由 Tauri 原生注册表持久化，只记录 Git 根目录、本机 runtime 地址和最近打开时间；按项目恢复会话 ID。Desktop 总是自动进入 General，发送普通任务无需目录；Agent 需要文件时以结构化事件请求 Scratch/Project，用户审阅后再切换。General、每个 Project 和每个 Scratch 都有稳定 runtime 身份与独立进程；切换只断开当前观察端，原 session actor、Prompt Queue 和后台任务继续运行，切回后按 sid/cursor hydrate 与补收事件。未保存编辑和正在等待保存确认仍阻止切换，普通 Agent 执行不再阻止。侧栏汇总前台、后台和异常恢复状态，2 秒监督循环检查全部受托管子进程。
- 已落地项目资产工作台第一阶段：Desktop 可查看当前真正会注入新会话的仓库记忆、超预算与脱敏状态；追加事实需再次确认并复用内核写入策略，当前会话 system 保持不变。Artifact 支持列表、历史版本、认证读取和静态沙箱预览，并可将 `@artifact:<id>` 放回输入框交给 Agent 继续迭代；无 token 时可打开既有服务端交互查看页。
- 已落地多 runtime 崩溃恢复、自动生命周期、内置 sidecar 与开发者预览打包：每个 runtime 启动时原子更新不含 token 的恢复注册表，子进程异常退出按 runtime 结构化标记；旧的单 runtime 记录自动迁移。重启后先尝试重新附着记录中的本机实例，失败后自动启动新引擎，不根据旧 PID 杀进程或执行任务。每个 runtime 使用独立回环端口与日志；首选端口不可用时自动申请空闲端口，同时托管数量有硬上限，精确停止不会影响其他项目。PyInstaller one-file sidecar 只暴露固定的回环 Gateway 入口，按当前 target triple 构建并优先于系统 `vc`；runtime 使用与 lock 完全一致的独立 venv，并从固定 URL、大小和 SHA-256 的 standalone CPython full archive 建立，验证上游元数据、架构、许可证和完整运行时树。真实 smoke 验证 health、Web 资源、release WebView、React、Tauri IPC 和 bundle 内 sidecar 启动；实际冻结组件与 CPython runtime 的第三方 notices、哈希和发布证据已结构化生成。正式分发仍需 notices 人工复核、Developer ID 签名、公证、升级策略和 Apple Silicon 原生 arm64 成品验收。

- 已落地一等 Goal 合同：目标、约束、非目标和验收标准持久化到 `.vortocode/goals/`；关联现有
  `TaskRunner`、`DevPlan`、分支与断点续跑，不增加第二套自治 runtime。
- 已落地证据完成闸门：自动测试/审查记录为执行证据，但不会擅自替代业务验收；Desktop 可逐项记录
  通过/失败证据，只有全部标准通过才标记 `achieved`，失败项可重新执行或 `dev_resume`。
- Goal 默认先保存为可编辑草稿，用户确认目标/约束/验收标准后才启动后台开发；执行后合同锁定，
  防止事后改验收口径。自动测试/审查证据可由用户一键采用到具体标准，仍保留显式确认。
- 已落地源码工作区第二切片：Git scope 文件搜索、UTF-8 只读预览、行范围选择、文件/范围上下文附件。
- 文件上下文不由 Desktop 直接拼正文；Gateway 逐个经过共享 `read_file` 能力、权限、hook 和审计后注入。
- 已支持以固定 VS Code/Cursor CLI 跳转到选中行，未找到时降级到系统默认应用；不解析任意 `$EDITOR` 或 shell 字符串。
- 已支持内嵌 UTF-8 编辑缓冲区、dirty 状态、Tab/保存快捷键、CRLF/LF 保持与放弃修改。
- 保存不由 Tauri 直接写盘：Gateway 先重验 Git 源码范围、能力与项目 permissions，发送 diff，经过统一确认门后原子替换；
  打开时和确认后各校验一次 SHA-256，外部改动冲突时拒绝覆盖。
- 每个开发会话自动分配 worktree。
- 内置终端、Browser Preview、文件树、符号搜索与源码只读预览。
- [已完成] 选中文件/最多 500 行代码加入会话上下文，并支持跳转到用户现有编辑器。
- [已完成] PR/CI 状态、diff 评论、失败后修复入口。
- [已完成] 当前工作区 hunk 级 stage/unstage/revert；内容稳定 ID 让上方新增 hunk 不再改变评论身份，操作仍携带所见 patch 哈希，diff 变化时 fail closed；baseline 和来源归因随 diff 返回。
- [已完成] Agent 前后台命令和 Command Hook 的文本文件副作用进入用户目录来源账本；审查标签可区分 Agent、Hook、用户、混合与未登记外部改动，账本只保存行哈希而不复制源码。
- [已完成] 后台任务交接按持久任务分支逐文件、逐 hunk 审查；接受保存有界证据，撤销使用所见 patch 哈希防过期、创建可审计提交，并以“审查后重验”作为 PR 交付闸门。
- [已完成] 可版本化团队审查策略：可选要求所有当前文本 hunk 完成精确接受；任务卡与审查面显示 accepted/total/pending，Draft PR 服务端在交付瞬间重新计算而非信任 UI 缓存。

### V2 · 常驻与远程

- 把当前轻量编辑缓冲区升级为 Monaco/LSP；保存继续走现有统一权限、diff、确认与冲突检测边界。
- 多 runtime 常驻、项目级端口/日志/恢复、凭据进程隔离与跨项目 Inbox 已落地；继续增加资源预算、逐项目凭据轮换与全局暂停/恢复。
- Journal 每日摘要、跨日周报、安全恢复、待决策队列、审计时间线、opt-in 系统通知和跨项目聚合已提前在 V1 落地；V2 继续做可配置通知规则。
- 定时任务与跨项目通知规则。
- TLS 远程 gateway、设备配对、移动端查看/批准。
- 本地文件语义索引；跨项目关系视图需由真实使用数据证明价值后再做。

完整 VS Code/Zed 克隆不在当前路线内；如果未来产品定位变成“替代 IDE”，需要另立里程碑评估
LSP、终端、Git、快捷键、插件和多窗口等完整成本。

## V0 验收

1. `npm run build` 通过。
2. `cargo check` 通过。
3. gateway 协议与三端契约测试通过。
4. 手工启动 `vc server` 后，Desktop 可恢复会话、发送一轮消息、展示计划/流式输出。
5. 模拟确认与 diff 事件时，允许/拒绝回传正确，污点提示不可被隐藏。
6. Desktop 关闭时，由它启动的 gateway 子进程被回收；外部已启动的 gateway 不受影响。
7. release `.app` 可构建，并由 bundle smoke 验证真实 WebView、React、Tauri IPC 和内置 sidecar；冻结 runtime 的 health 与 Web 资源通过独立 smoke，开发者预览 `.dmg` 可生成，preview/release 发布证据明确区分未签名产物和满足全部闸门的正式产物。

当前自动联调已验证真实 Gateway health、`init v9`、hydrate `status v9`、workdir 和会话列表；
真实模型回合仍需在配置用户 API Key 后做 GUI 验收。
