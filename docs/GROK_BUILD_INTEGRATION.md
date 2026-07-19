# grok-build 融合计划

> 上游：<https://github.com/xai-org/grok-build>  
> 本轮核对基线：公开仓库 `8adf9013a0929e5c7f1d4e849492d2387837a28d`，对应
> `SOURCE_REV=2ec0f0c8488842da03a71eeee3c61154957ca919`（2026-07-17）

VortoCode 会吸收 grok-build 已验证的交互与运行时思想，但不会把两套产品机械拼接。
VortoCode 继续以 Python Gateway 作为唯一 Agent runtime，以 Tauri Desktop、CLI/TUI 和 Web
作为共享协议上的不同客户端；grok-build 的 Rust TUI、模型供应商实现和品牌资源不直接成为
第二套内核。

## 融合原则

1. **会话是长期 actor，不是某条 WebSocket。** 执行、队列、权限请求和事件日志都归会话；
   Desktop 只是可随时附着/离开的观察与控制端。
2. **服务端状态权威。** Prompt Queue、当前回合、等待确认、后台任务、hunk 身份和验收证据
   都由 Gateway 产生快照与增量事件，客户端不靠本地布尔值猜状态。
3. **行动优先的 Agent Dashboard。** 等待人处理的会话排在工作中会话之前，再到暂停队列、
   空闲和仅落盘会话；活动、目录、分支、worktree 和模型模式逐步成为行级摘要。
4. **后台完成会唤醒主会话。** 子 Agent、命令和测试完成后写入结构化结果，并通过合成事件唤醒
   leader，而不是要求用户反复刷新或手工追问。
5. **Git 变更具有稳定身份和来源。** hunk 在内容移动、相邻编辑和 baseline 更新后仍尽量保持
   稳定 ID，并区分 Agent 回合产生的改动与外部编辑，支持可靠的接受、撤销、评论和交接。
6. **扩展点只拿数据与能力。** 生命周期 Hook 接收结构化输入，通过注入能力执行动作，但不拥有
   Agent 主循环控制权；失败默认不破坏会话状态机。

## 模块映射与进度

| grok-build 能力 | VortoCode 落点 | 状态 | 代码来源 |
|---|---|---|---|
| Prompt Queue / queue changed | Gateway 协议 v8、会话持久队列、Desktop composer | 已落地第一阶段 | 按语义重新实现，未复制上游源码 |
| Agent Dashboard / roster row states | `/api/agent/sessions` 权威状态快照、Desktop 任务侧栏 | 已落地任务关联阶段 | 会话行展示 cwd、branch、worktree、归属后台任务与上下文占用，并可直达任务交接；按状态模型重新实现 |
| Session actor / 多客户端订阅 | 回合与 WebSocket 解耦、subscriber registry、断线续跑 | 已落地第一阶段 | 适配 VortoCode 异步架构 |
| Event replay / roster | 单调 `seq`、append-only 分段 journal、`after_seq` cursor replay | 已落地 | VortoCode 原生持久事件层 |
| Background task / auto-wake | TaskRunner、Worktree、所属会话结构化交接 | 已落地第一阶段 | 协议 v8 `task_handoff` 持久唤醒；默认不隐式启动新 Agent 回合 |
| Agent lifecycle contributors | `src/hooks`、协议 v8 `agent_hook`、Desktop 时间线 | 已落地能力收敛阶段 | 项目 Hook 显式信任、调用级能力注入、深拷贝事件、被动 Hook 不写回主循环、Web/Desktop 热重载 |
| Hook failure queue | Dashboard 权威聚合、决策中心定位/确认 | 已落地 | 失败/超时/阻止从持久活动投影，确认状态保存在仓库外且不复制错误正文 |
| Hunk tracker | Git review stable hunk ID、baseline、turn/source attribution | 已落地实时失效阶段 | 协议 v9 推送 `git_review_changed`；Desktop 重取权威 diff，精确 patch hash 继续作为执行闸门 |
| Task branch review | 任务交接、持久 `vorto/*` 分支、稳定 hunk 决策与隔离重验 | 已落地团队策略阶段 | 活跃 worktree 只读；可选团队策略要求当前分支全部文本 hunk 有精确接受证据，交付时实时重验 |
| Rust TUI 渲染、xAI provider、品牌资源 | 不融合 | 明确排除 | 与现有技术栈或产品身份不匹配 |

## 当前成熟度差距

这里比较的是用户可感知的产品成熟度，不按源码行数或功能勾选数量计算。按 2026-07-17 公开版本，
VortoCode 相对 grok-build 整体约处于 **55%–65%**；核心开发闭环已经过半，但扩展生态、标准接入、
安装更新和高频交互仍有明显距离。

| 维度 | 相对成熟度 | 判断 |
|---|---:|---|
| Agent 工具循环、Plan/Build、权限与上下文 | 70%–80% | 主闭环已具备；模型后端细节、压缩/rewind/fork 和长期稳定性仍弱于 grok-build |
| 会话 actor、队列、后台任务、worktree | 80%–90% | 队列、断线续跑、跨项目 runtime 与交接已经接近；资源调度和周期任务仍缺 |
| Desktop Goal/Git/PR 控制面 | VortoCode 更强 | grok-build 以 TUI 为主；VortoCode 的结构化验收证据、跨项目 Inbox、逐 hunk 审查是差异化优势 |
| 高频交互与故障恢复 | 50%–60% | thinking/tool 时间线已起步，但首 token、重试、取消、错误解释、键盘流和边界状态还需系统 QA |
| Skills/Plugins/Hooks/MCP/LSP 生态 | 30%–40% | VortoCode 有 Hook/MCP 内核，但没有统一发现、诊断、安装、启停和 Marketplace 产品面 |
| Claude/AGENTS 兼容与标准嵌入 | 40%–50% | 有 `CLAUDE.md`/仓库规则基础，缺完整兼容扫描、Claude 导入和 ACP stdio 标准入口 |
| 配置、认证、分发与更新 | 25%–35% | 模型快捷设置可用，但缺 browser/device auth、managed policy、稳定签名、公证和自动更新通道 |
| TUI/CLI 自动化成熟度 | 45%–55% | 有 CLI/TUI/Web 共享内核，缺 streaming-json 契约、完整 inspect/doctor、session export/import 和 shell completion |

公开 grok-build 已将这些能力做成统一产品面：`inspect` 可解释 rules/skills/plugins/hooks/MCP 的来源，
插件同时承载 skills、agents、hooks、MCP 与 LSP，并提供 Marketplace；CLI 同时支持 headless JSON、
ACP stdio、session/worktree 管理、更新和 Claude Code 配置兼容。VortoCode 不应复制其品牌和 Rust TUI，
但应把这些“可发现、可诊断、可组合”的合同吸收到 Desktop-first 架构。

第一阶段 Dashboard 状态为：

- `needs_input`：存在待人工确认，最优先展示；
- `failed`：存在失败/暂停后台任务，或尚未确认查看的 Hook 失败、超时与阻止事项；
- `working`：会话回合或确定性工作区操作正在执行；
- `queued`：没有前台回合，但仍有停止后保留的待运行输入；
- `idle`：当前 runtime 已加载、可继续交互；
- `inactive`：仅磁盘持久化，尚未加载到当前 runtime。

Dashboard 不从消息文案臆测完成与失败：`failed` 只来自归属该会话的持久后台任务明确进入
`failed`、`paused` 或 `interrupted`，或结构化 Hook 生命周期进入 `failed`、`timed_out`、`blocked`；
`completed` 仍等待会话 actor 自己产生终态。上下文百分比
来自 `MainAgent.context_usage()` 对下一次模型调用的只读估算，并随会话持久化，刷新列表不会推进
裁剪游标。Git cwd、branch、HEAD 和 worktree 关系由 Gateway 读取，Desktop 不自行执行 Git 猜状态。

后台开发 worktree 还会写入一份有界生成态关系账本，只保存 `worktree_id → task_id / owner_session /
plan_id`，不复制提示词或源码。关系从后台 worker 的执行上下文传入 Git worktree 创建线程，因而并行
任务不会串单；Desktop 刷新或 Gateway 重启后仍能把尚存的临时 worktree 投影回对应任务。任务列表、
Dashboard 会话行、持久计划和实时 worktree 共用这份服务端投影，点击会话的后台状态、计划或 worktree
都能直接定位到同一任务交接卡。

任务交接现在可以直接审查对应的持久任务分支，而不是修改 Agent 仍在使用的一次性 worktree。运行中、
暂停、失败或取消的任务保持只读；只有成功完成的任务允许记录“接受”证据或撤销 hunk。撤销会在隔离
worktree 中反向应用用户看到且 SHA-256 仍匹配的 patch，生成 `review:` 提交后以旧 HEAD 作为
`update-ref` 前置条件更新 `vorto/*` 分支。任何撤销都会使旧验证证据失效；重新在该精确 HEAD 的
隔离 worktree 中测试通过前，后台任务的 Draft PR 入口 fail closed。审查账本只保存 hunk 身份、
哈希和有界测试摘录，不复制源码。

仓库可选择版本化 `.vortocode/review-policy.yaml`，开启团队级“全部 hunk 已决策”门禁：

```yaml
task_branch:
  require_all_hunks_decided: true
```

默认关闭，不改变个人开发流。开启后 Desktop 展示当前精确 patch 的 `accepted/total/pending`，未完成时
禁用 Draft PR；服务端交付端点仍会重新读取当前 `vorto/*` HEAD 和完整文本 diff，不信任卡片缓存。
配置语法无效、diff 无法读取或超过安全上限都会 fail closed。接受证据必须同时匹配稳定 hunk ID 和
patch SHA-256；分支后续增加或改变 hunk 会再次关闭门禁。生成态覆盖率缓存位于任务审查账本，策略文件
本身属于用户配置，可纳入版本控制。

生命周期 Hook 现在同样使用显式能力合同。Runtime 按事件和实现注入 `observe_event`、
`emit_annotation`、`block_tool`、`run_command`、`send_http` 或 `request_model`；仓库配置只能缩减。
每个 Hook 接收独立的深拷贝事件快照，`modify_data` 不再写回主循环，被动事件的 stop/message 不能
进入模型工具结果或终止 Agent。Desktop 的信任预览展示有效能力，缺少 action 能力时动作不会开始。

Hook 的失败、超时和明确阻止现在会从有界活动历史投影成 Dashboard 待处理项，并进入 Desktop
决策中心。用户“已查看”只会在用户应用数据目录记录 Hook lifecycle ID；仓库内容不能伪造确认，
错误正文、命令输出和源码不会复制到确认账本。删除会话时对应确认状态一并清理。

Project/Scratch 会话附着 Desktop 后，Gateway 会以每会话一个轻量 watcher 观察 HEAD、逻辑 index、
改动路径元数据和来源账本 revision。变化通过协议 v9 `git_review_changed` 唤醒 Desktop，客户端随后
重新请求权威 snapshot/diff；事件只持久 cursor 占位，避免重启后回放已经过期的文件状态。watch token
不会因只读 `git status`/diff 刷新而变化，hunk 写操作仍必须通过所见 patch SHA-256 的精确校验。

## 直接复制代码时的许可证规则

grok-build 一方源码使用 Apache License 2.0。该许可证允许复制、修改和分发，也能与 VortoCode
的 MIT 原创代码共同分发，但复制部分仍受 Apache-2.0 约束，不能被标成 MIT-only。

若后续直接复制任何实现，合入前必须同时完成：

1. 固定上游 commit 和原始文件路径，确认它是一方 Apache-2.0 代码，而非 `third_party`、
   vendored 或带独立 notice 的移植代码；
2. 在修改文件中保留适用的版权与归属声明，并显著说明 VortoCode 修改过该文件；
3. 随分发产物提供 Apache-2.0 许可证；上游或该组件存在 NOTICE 时，保留所有仍适用的 NOTICE；
4. 把来源、commit、修改摘要登记到 VortoCode 的第三方 notices；
5. 不复制 xAI/Grok 名称、logo、图标或其他商标性产品身份。

当前表中标为“已落地第一阶段”的模块均为基于公开接口与行为的 VortoCode 原生实现，尚未复制
grok-build 源码，因此本轮不新增 Apache 源码归属文件。第一次直接复制发生时，许可证与 NOTICE
必须和代码在同一个变更中进入仓库及 Desktop 分发清单。

## 后续交付顺序

1. 先完成 Desktop 稳定开发签名、Keychain 单次缓存、正式更新通道与启动/模型请求可靠性；
2. 建立统一 Extensions / Inspect 中心：展示规则来源、Skills、Hooks、MCP、Agent 与后续 Plugin/LSP，
   提供诊断、信任、启停和来源优先级，不先做无治理的 Marketplace；
3. 补齐会话产品能力：fork、rewind、compact、搜索、导出，以及后台循环/定时任务；
4. 为 `vc` 增加稳定 streaming-json 与 ACP stdio 适配器，让 Desktop、IDE、CI 共享同一事件合同；
5. 在已落地的多 runtime / 多项目常驻基础上增加资源预算、逐项目凭据轮换与全局暂停/恢复；
6. 把团队审查策略扩展为版本化的 lint/test/保护分支组合，并为 Hook/扩展能力增加组织级上限。
