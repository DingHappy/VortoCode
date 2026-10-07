# Desktop 首次交付验收

## 任务下派入口检查（2026-10-03）

本地 Desktop 已接入只读研究下派服务，表单提供任务类型、角色、验收要求和执行步数；
服务任务卡提供直接验收、返工、下一轮和取消。提交逻辑已提取为 useTaskDispatch。

自动验证：132 项前端测试通过，TypeScript 与 Vite 构建通过。浏览器使用临时组件预览和
模拟 Gateway，实际点击验证研究提交、响应丢失后相同 request_id 重试、查看结果、
第一轮 rework、同 ID 下一轮执行以及第二轮 accept。观察请求中的 owner 与 round 均正确。
组件截图检查发现表单在浅色主题下继承文字颜色导致对比度不足，已补显式文本颜色。
预览文件和本机服务已清理，浏览器测试空间已关闭。

这些是模拟服务的组件交互证据，不是已安装 Desktop 包的真实模型验收。仍需在新构建的
原生包中检查：研究角色返回真实证据、取消运行中任务、网络中断/重连、切项目后旧响应不串入、
原生明暗主题与任务卡操作，以及拒绝过期轮次的用户反馈。

## 任务协作回归场景（2026-10-03，待真实 GUI 验收）

在明确选择的 Project 会话中委派一个小型只读任务，核对任务卡的执行角色、任务 ID、
轮次、结果与“待验收”状态。主 Agent 应收到结构化结果后继续处理；接受结果只改变
协作验收状态，不代表代码验证或 Goal 达成。

在主 Agent 尚未接受结果时点击“补充要求 / 返工”，确认切回任务所属会话、草稿包含
任务 ID 与旧轮次、没有自动发送且未覆盖其他输入草稿。补充要求发送后，任务 ID 保持
不变、轮次增加，新结果重新待验收。dev 角色返工仍需确认；取消或拒绝后不能显示成功。
重启后应能读取消息与结果；使用旧轮次的审查/返工必须拒绝。General 不能访问仓库任务。

本场景的真实模型、原生包和点击证据尚未采集；单元测试和前端构建不能代替这些证据。

每次改变 Desktop 主链路、打包 runtime 或隔离开发工具后，用一个一次性 Git 仓库完成下面的验收。静态 GUI smoke 和单元测试分别是必要证据，不能代替真实模型交付。

## 场景

1. 在目标架构的机器上构建 `.app`，运行 `npm run sidecar:smoke` 与 `npm run smoke:bundle`，检查内置 runtime、React 和 Tauri IPC。确认 bundle 内主程序及 sidecar 的架构与机器一致。
2. 从 Desktop 打开新包，确认模型配置状态和 General runtime 就绪；发送一条不调用工具的短消息，记录是否收到真实模型回复。
3. 新建独立 Git 项目，预置一个失败的测试。通过 Desktop 选中项目，发送明确的修复任务。记录规划、确认、隔离实现、自测、失败重试和最终回复。
4. 独立检查主分支未变、交付分支存在、diff 与测试结果一致。尝试从 Desktop 打开交付分支的 diff；如果必须借助命令行，记为 Desktop 交付链路未闭合。

记录每一步的成功/失败、耗时、人工确认次数、模型和已知费用；费用缺少价格时记为未知。不要把测试通过写成业务目标已验收，也不要把 smoke 通过写成完整用户验收。

## 2026-09-27 本机验收记录

- 机器为 Apple Silicon；此前安装的 x64 包无法加载内置 Python 动态库。未把旧包的启动失败归因于当前 arm64 源码。
- 当前源码的 arm64 sidecar 与 GUI bundle smoke 均通过；Desktop 从 Keychain 读取到已配置的模型服务，一次真实短消息得到预期回复。
- 一次性 Python 仓库的失败测试由 Agent 在隔离分支修复。独立检查：主分支干净，diff 只有 `pricing.py` 一行替换，两个测试通过；没有远端，也没有创建 PR。
- 交付用时约 5 分钟。Agent 重复读取同一文件，首次把测试函数名误传为文件路径，重试后成功。首次确认发生在规划预算耗尽时，当时没有可审阅的计划；最终回复又把一行替换误报为“9 行改动”。这些是需要继续观察的质量问题。
- Desktop 的“变更”面板只显示主工作区干净，无法从这次 `dev_isolated` 交付直接打开隔离分支 diff；这一段尚未通过 GUI 验收。

本轮针对可复现问题修正了 sidecar smoke 的安全默认断言、pytest 裸函数名选择器、预算耗尽时的确认文案，以及把 diff 元数据误计为代码改动行数的报告。再次验收应使用含这些改动的新包，尤其复查规划确认与分支审查。

## 2026-09-27 隔离交付审查补充

`dev_isolated` 成功落分支后，现在会在项目的 `.vortocode/isolated_deliveries/` 保存分支、固定基线、交付提交、验证命令和经过凭据脱敏的输出。Desktop“变更”面板可列出这些交付，按文件打开当前分支相对固定基线的 diff；分支 HEAD 变化后，面板会标明原验证证据已经过时。记录失败会在工具结果里明示，分支本身仍保留。

实现和本地自动检查已完成；arm64 `.app` 已构建，sidecar 与 GUI bundle smoke 通过。还需用新构建的 Desktop 包重复上述真实模型任务，确认交付卡在成功后自动出现、文件 diff 可读、测试输出准确；此前那次交付发生在此记录机制加入之前，不应把它算作新界面的通过证据。本次原生 UI 自动化管道未启动，不能据 bundle smoke 宣称交付卡已经过人工点击验收。

## 2026-10-03 任务数据域重构验证

任务列表、选中任务、工作区快照与暂停/恢复/取消迁入 useTasks；协议分发、通知、导航
仍由 App 装配。受控异步测试覆盖旧 HTTP 响应与实时更新竞争、请求乱序、连接切换、
项目重置、任务 ID 去重与请求失败降级。

本次 npm run check 通过：139 项 Vitest 测试、TypeScript/Vite 构建、runtime 入口检查、
sidecar 构建检查、61 项 Rust 测试及 cargo check。Rust 本地端口测试在允许端口的环境下
重跑通过。未重新打包/安装原生应用，未执行真实模型或原生点击验收；上述检查不替代这些证据。

### 同日：任务面板与并发连接补充

TasksPanel 已完整迁出 App，保持入口、工作区/计划列表、任务卡和定位交互。
连接适配新增握手/断开代次校验，拒绝旧连接事件、旧握手/失败与迟到断开结果。
静态组件渲染验证离线下派、委派待验收及过期验证的 PR 闸门；受控异步测试验证连接竞争。

完整 npm run check 通过：147 项 Vitest 测试、61 项 Rust 测试、TypeScript/Vite、runtime
入口、sidecar 构建检查与 cargo check。本次仍未重新打包/安装或采集原生点击、真实模型证据。

### 同日：开发/研究完成交接统一

HTTP 下派研究现进入原会话的持久完成收件箱，主 Agent 可在用户回合读取、审查并记录
处理回执。任务卡分别展示验收与处理状态；新轮次相同文本仍为新交付，旧回执拒绝。
模型返工工具不绕过服务入口的步骤、超时与共享并发限制。

自动化通过：3010 项 Python 单测（2 跳过）、54 项集成测试、149 项 Vitest、61 项 Rust；
完整 npm run check 与 Ruff/diff 检查通过，sidecar 已重新构建。测试使用假 worker/LLM，
未安装原生包或执行真实模型点击验收，没有启动自动付费回合。

### 同日：有界检查预算执行器

新增严格请求预算代理与单次只读 MainAgent 检查执行器，默认 provider 不符合经过验证的
适配合同，自动检查保持关闭。未添加授权界面、认领调度或自动付费模型调用。
验证请求前预留、计量不可靠时拒绝、并发余额、工具/收尾限制、取消和未知用量保留。
asyncio 超时为协作式取消，不作为阻塞工具强制墙钟隔离证据。

22 项针对性预算测试通过；完整回归为 3024 项 Python 单测（2 跳过）、54 项集成、
149 项 Vitest、61 项 Rust。npm run check、sidecar 构建、Ruff/diff 检查通过。
尚未采集真实 provider 计量/费用、原生授权点击及自动检查的用户级验收证据。

### S2 持久授权/认领后端切片

结果版本绑定授权、一次认领、撤销、迟到回写拦截和启动中断对账已接入原任务台账。
本次完整 Python 单测与集成回归共 3090 项通过、2 项跳过；另补充的记录容量与未认领恢复
场景在 14 项认领专项测试中通过。Ruff 与 git diff --check 通过。
没有修改 Desktop 交互或启动真实模型，未重复运行前端/Rust 构建；HTTP/UI 授权入口、
会话 actor 自动汇报、严格 provider 适配及原生点击验收仍待完成，自动检查保持关闭。

### S2 actor 检查桥与能力报告切片

检查桥复用现有会话 actor 槽位，覆盖前台优先、停止保留队列、首时隙前取消、重复观察端、
资格变化、结果变化、撤销后用量、未知请求预留、取消被吞掉和汇报/回执失败。
24 项桥接专项测试通过；完整 Python 单测与集成共 3116 项通过、2 项跳过。
Ruff 和 git diff --check 通过。Desktop sidecar 重新构建，并在临时工作目录验证打包服务
启动、恢复模块导入及 /api/delegations/capabilities 的自动检查关闭状态，未调用模型。

证据日志：/tmp/vorto-actor-check-regression.log、/tmp/vorto-actor-sidecar-build.log。
没有改动本轮 Desktop 交互，未重复前端/Rust 测试，未安装或发布应用。
生产合格 provider、会话持久汇报 writer 和 HTTP/模型工具/Desktop 授权入口尚未装配；
检查桥测试使用声明能力的假适配器，不是实际 provider 的计量、费用或用户点击验收。

### S2 原会话持久汇报与只补发恢复切片

CheckReportWriter 复用原会话 transcript，按尝试身份去重。保存失败不修改内存展示消息；
报告严格核对所属会话、结果版本、授权/尝试与持久结论/预算，保留 tainted 来源。
已完成检查可通过 resume_report 补齐报告保存/交接回执，不重新调用模型。Web writer 已
适配到 agent_emit，后端重建后的 agent_history 回放测试包含原报告及来源。

完整 Python 单测与集成回归 3132 项通过、2 项跳过；最终 19 项汇报专项测试包括补充的
文件名身份别名、损坏尝试字段和实际 Web 历史回放。Ruff/diff 检查通过。
sidecar 已重新构建，冻结模块清单包含 src.gateway.check_reports；临时工作目录中的
打包服务启动、恢复导入和能力接口 smoke 通过。没有真实模型调用、前端交互修改、
原生应用安装或部署，未重复前端/Rust 测试；自动检查仍关闭。

证据日志：/tmp/vorto-check-reports-regression.log、/tmp/vorto-check-reports-sidecar-build.log。
剩余范围：合格 provider、显式授权/停止 UI、生产完成事件装配与报告补发操作入口。
展示输出已持久化不表示用户已读；历史沿用最近 200 条上限，不宣称断电或多进程保证。

### 会话生命周期、容量与删除保护切片

前台回合和后台检查共用唯一任务登记与收尾包装器。前台首时隙前停止保留原输入，
通知原请求并释放槽位；已进入执行的回合不自动重放。清理期间仍占用槽位，重复停止
更新原因，迟到收尾不清除新回合。恢复窗口预留一个队列位置，总上限仍为 20，满额
重复请求保持幂等；立即执行继续优先于 FIFO。

会话容量满时保护运行、检查清理、优先输入、FIFO 与待确认状态；稳定 SID 的空闲会话
必须先保存成功才可淘汰。保存失败跳过候选，工厂/恢复失败保留原会话，没有安全候选
则明确拒绝创建。实际 WebSocket 路由测试验证 agent_error、1013 关闭与连接清理；
发送/关闭失败也会解除连接和订阅。明确删除已停止的会话同时清除 actor 残留的优先
输入和停止标记，避免复用 SID 再次执行已删除输入；活动及清理期间仍拒绝删除。

最终完整 Python 单测与集成回归 3169 项通过、2 项跳过，173 项针对性回归通过。
Ruff 与 git diff --check 通过。sidecar 已重新构建，临时工作目录中的打包服务启动、
恢复模块导入和自动检查关闭的能力接口 smoke 通过，没有调用模型。

证据日志：/tmp/vorto-session-lifecycle-regression.log、
/tmp/vorto-session-lifecycle-sidecar-build.log、/tmp/vorto-actor-sidecar-runtime.log。
本轮仅修改后端与文档，未重复前端/Rust 测试，未安装或发布原生应用；原生点击、真实
provider 计量与自动检查授权验收仍待完成。普通保存仍是尽力而为、优先输入仍驻内存，
临时 ws-id 不落盘，不宣称全部输入持久化、断电恢复或多进程共享状态。

### 交接回执、预算认领与任务身份完整性切片

缺说明或有效有时区处理时间的回执仍视为未读，匹配版本字符串不能单独隐藏交接；
原确认操作可补齐回执。预算合同缺字段不使用默认值，已有尝试/用量/结束标记的授权
即使状态误改也不允许再次认领。完成和补发必须具有精确有效尝试身份、完整结清预算
且未超 token 上限；失败记录仍保留真实超额用量与未知调用预留，不封顶抹去消耗。
TaskLedger 统一核对精确请求 ID、文件名与记录 ID，身份别名或损坏文件不参与查询、
认领、交接确认和启动恢复，保留原文件，不自动改写其他任务。

175 项专项回归通过；完整 Python 单测与集成回归 3215 项通过、2 项跳过。
Ruff 与 git diff --check 通过。sidecar 重新构建，并在临时工作目录验证打包服务启动、
恢复模块导入及能力接口，自动检查仍关闭。provider 违规与超额用量场景使用假适配器，
没有真实模型调用，不能作为实际 provider 的费用或计量验收。

证据日志：/tmp/vorto-handoff-integrity-regression.log、
/tmp/vorto-handoff-integrity-sidecar-build.log、/tmp/vorto-actor-sidecar-runtime.log。
本轮仅修改后端与文档，未重复前端/Rust 测试，未安装或发布应用；生产预算 provider、
显式授权界面、完成事件装配和原生点击验收仍待完成。不完整记录不会自动重新执行检查。

### 手动交接 HTTP 与 Desktop 入口切片

任务卡“记录交接处理”支持填写说明并“标记已处理”，与任务验收分别记录。
GET /api/task-inbox 和 POST /api/task-inbox/{id}/acknowledge 直接适配既有 CompletionInbox；
开发和 API 研究共用原台账。身份、终态、精确版本和持久化校验均由服务端执行。
只有首次成功保存广播 task_update；同版本重试保留首次回执，不发布新的完成事件。

完整 Python 单测与集成回归 3232 项通过、2 项跳过；前端 167 项与 Rust 61 项通过。
Desktop build、运行入口检查、cargo check、sidecar 构建、Ruff 和 git diff --check 通过。
错误提示/刷新按钮收尾后重跑前端构建及全部前端测试、18 项接口/路由测试，并重建 sidecar。
最终打包服务在临时工作目录通过收件箱查询、写回执、重复确认、台账读取验证；自动检查关闭。

ego-browser 使用真实 TaskCard 组件、浏览器连接适配夹具和临时 HTTP 服务验证：

- 保存失败显示错误、保留说明，恢复存储后可重试；成功后显示交接已处理，研究仍待验收。
- 结果版本变化拒绝旧确认；刷新后显示新轮次，清空旧结果的说明。
- 在途重复提交不会发出第二个请求；任务或连接切换后，迟到响应不刷新新范围或覆盖新说明。
- 不匹配的回执不显示成功，保留说明并提供刷新。

证据：/tmp/vorto-manual-handoff-regression.log、/tmp/vorto-manual-handoff-desktop-check.log、
/tmp/vorto-manual-handoff-sidecar-final-build.log、/tmp/vorto-manual-handoff-sidecar-smoke.log、
/tmp/vorto-manual-handoff-browser-races.json、/tmp/vorto-manual-handoff-conflict.png。
临时夹具和服务已清理。浏览器使用真实组件与 API，迟到/不匹配回执由夹具控制；
这不是打包原生 Desktop 或 Tauri HTTP 插件的点击验收。没有真实模型调用、安装或发布应用。
生产预算 provider、显式自动检查授权和完成事件调度仍待独立验收。

### 服务研究的问题、回答与阻塞恢复切片

服务研究的前两轮提供 ask_task_question；问题、选项、已有发现、提问轮次和 24 小时期限制
保存在原任务台账。问题落盘后立即退出模型回合并释放共享执行槽，任务显示等待回答。
TaskCard、决策队列和运行时收件箱可见 blocked；未完成任务不进入完成交接或验收流程。
回答通过 /api/delegations/{id}/answer 保存后使用下一轮预算，最多三轮、两次提问。
角色与权限恢复前再次校验；相同答复重试不重复执行，取消/过期/错归属/错轮次拒绝恢复。

最终完整 Python 单测与集成回归 3259 项通过、2 项跳过；前端 182 项、Rust 61 项通过。
Desktop build、运行入口检查、cargo check、Ruff 和 git diff --check 通过。新增回归覆盖
真实模型循环配假 LLM 的暂停、同批后续工具停止、native 工具路径、权限拒绝、答复上下文
重建、共享槽释放、有限轮次、持久化失败、恢复前取消及重启不自动重放。

ego-browser 使用真实 TaskCard 与隔离 HTTP 服务，由假 LLM 驱动完整提问—回答—继续执行：

- 等待时模型调用计数 1、活动槽 0；保存失败保留回答并维持此状态。
- 模拟有效回答响应丢失后重试，模型调用计数总计 2，第二次提交不重跑；完成后仍待验收。
- 过期回答拒绝；取消问题不启动模型；任务/连接切换后的旧响应不覆盖新草稿。
- 在途重复点击只发出一次请求；共用请求 hook 的手动交接表单仍正常保存。
- 在 Desktop 最小窗口约束内，346px 宽任务卡及 324px 宽问答区没有横向溢出。

最终 sidecar 在临时工作目录通过问题能力/查询、过期回答拒绝、取消及原有手动交接 smoke。
打包 smoke 未提交会启动模型的有效回答；有效回答执行链路由源代码测试和浏览器假模型验证。
证据：/tmp/vorto-task-questions-final-regression.log、/tmp/vorto-task-questions-desktop-check.log、
/tmp/vorto-task-questions-final-sidecar-build.log、/tmp/vorto-task-questions-sidecar-smoke.log、
/tmp/vorto-task-question-browser-flow.json、/tmp/vorto-task-question-browser-layout.json、
/tmp/vorto-task-question-narrow-column.png。临时页面、服务和仓库内夹具已清理。

未调用真实付费模型，未安装或发布应用；浏览器连接适配不代表打包原生 Tauri 的点击验收。
模型回合内委派、dev 角色问答与主 Agent 直接回答工具尚未接入；生产自动检查继续关闭。

### 主 Agent 回答与服务装配收敛切片

Web/Desktop 主 Agent 增加 task_answer，通过当前会话身份与问题精确轮次回答服务研究问题。
HTTP 和模型工具共用 web/task_dispatch.py 装配的 DispatchService、事件通知和原 TaskRunner，
没有新增并发池。下一次用户回合提示检查所属有效问题；问题/完成事件不自动启动模型。
未知偏好或授权仍须询问用户。CLI/IM/TUI 未接线时不提供工具，General 无目录会话不暴露工具。
端口一致性测试明确登记此服务能力差异，其他工具差异仍受原契约检查。

新增 20 项回归覆盖真实主循环的 prompt/native 问题读取与答复回灌、前台不等待后台、
动态会话身份、同答复跨模型/HTTP 重放、权限/capability 拒绝、错轮次/问题、过期、取消、
角色变化、存储/容量/在途执行拒绝，以及真实受限子 Agent 的问答重建和预算继承。
定向 135 项和端口一致性/问答 27 项通过，Ruff 与 git diff --check 通过。
最终完整 Python 单测与集成回归 3279 项通过、2 项跳过、11 项既有弃用告警。

sidecar 重建后，在临时工作目录启动本机确定性假模型 HTTP 服务和打包 runtime，
通过真实 HTTP 与 WebSocket 驱动完整链路：API 研究提问进入 blocked；连接与等待不启动
主 Agent；用户输入后主 Agent 读取 task_status、调用 task_answer，后台进入第 2 轮完成。
主 Agent 请求 3 次，子 Agent 请求总计 2 次；同一答复经 HTTP 重放后次数不增加，
结果为 done、review=pending，自动检查仍关闭。打包链路使用提示式工具协议，native
回灌由源代码配假 LLM 单测验证。临时工作目录、进程和假模型服务已清理。

证据：/tmp/vorto-task-answer-focused.log、/tmp/vorto-task-answer-parity.log、
/tmp/vorto-task-answer-final-regression.log、
/tmp/vorto-task-answer-sidecar-build.log、/tmp/vorto-task-answer-packaged-loop.log、
/tmp/vorto-task-answer-packaged-loop.json。未修改 Desktop 前端/Rust，本轮未重复其测试。
未调用真实模型，未安装或发布应用，也未进行打包原生 Desktop 的人工点击验收。

### 开发暂停与持久恢复基础切片

原开发恢复入口收敛到 gateway/task_recovery.py，复用 TaskLedger、TaskRunner 和隔离
dev_resume。一次来源只创建一个恢复子任务；重复请求、完成后重试及重启后查询不重新
执行。合同保存来源/计划摘要，执行前版本变化拒绝；损坏身份、错归属、活动计划、满队列
和存储失败不能换新身份绕过。恢复任务可独立交接，旧来源保留历史并退出重复注意事项。

开发结果以持久计划判定：未完成块、集成失败和审查阻塞记 failed，保留工具说明。
TaskRunner 的排队/开跑/停止/终态和 DevPlan 检查点必须保存成功。暂停时同步 Git/测试
线程先退出，再释放共享槽、Git 锁和工作树；创建阶段取消也清理，重复取消不能提前收尾。
暂停可能等待当前测试或原有超时，没有新增强杀线程机制。

本切片新增后端 51 项回归，定向开发/工作树/任务回归 161 项通过。最终完整 Python
单测与集成回归 3330 项通过、2 项跳过、11 项既有弃用告警；前端 183 项通过。
Desktop build、运行入口检查 5 项、最新 sidecar 构建、Ruff 与 git diff --check 通过。
Rust 代码未修改，本轮未重复 Rust 检查。

最新打包 runtime 使用临时 Git 仓库、本机确定性假模型和真实 HTTP 验证：

- 普通恢复跳过已 landed 块，只实现一块待办；模型请求 2 次，实际隔离测试及最终集成
  测试通过，重复恢复不增加请求，恢复任务有独立交接回执，原 main/工作区保持。
- 暂停夹具在真实 pytest 内设置可控等待门；测试未退出时暂停 HTTP 尚未返回，任务仍
  running 并保留活动槽。释放等待门后返回 paused，未提交待办块，工作树已清理。
- 从这个最新暂停任务显式恢复完成；合计模型请求 4 次，两次已授权实现各 2 次；旧来源
  重试返回原暂停子任务，最新来源重试返回完成子任务，均不额外执行。集成 3 项测试
  通过，旧来源注意事项计数归零，完成回执不改变执行状态。
- 实际测试使用 macOS Seatbelt，证据为 policy=auto、isolated=true、fallback=false。
  临时工作树和服务清理；Git 操作与提交仅发生在测试夹具仓库，项目仓库未提交。
- 原研究问答/主 Agent 答复打包链路仍通过：主 Agent 请求 3 次、子 Agent 请求 2 次，
  最终 done/round=2/review=pending，同答复重试不重跑。

证据：/tmp/vorto-dev-recovery-cancel-focused.log、/tmp/vorto-dev-recovery-final-regression.log、
/tmp/vorto-dev-recovery-final-frontend.log、/tmp/vorto-dev-recovery-final-desktop-build.log、
/tmp/vorto-dev-recovery-final-sidecar-build.log、/tmp/vorto-dev-recovery-packaged-loop.json、
/tmp/vorto-dev-pause-packaged-loop.json、/tmp/vorto-task-answer-packaged-loop.json。

未调用真实付费模型，未安装或发布应用，未做打包原生 Desktop 人工点击验收。
本轮的模型执行使用提示式工具协议；native 回灌仍由既有源码配假 LLM 回归验证。
Git 提交与计划检查点不是跨资源原子事务，崩溃窗口仍需核对分支/块证据；多进程事务、
dev 结构化提问、模型回合内委派暂停和生产自动检查尚未开放。

### 后台开发 Agent 结构化问答与原任务接续（2026-10-04）

明确所属会话的 dev/dev-resume 已支持 ask_task_question。问题固定任务、轮次、计划、块、
分支与摘要；running 阶段先保存问题，隔离执行器清理后才由原 TaskRunner 保存 blocked
并释放槽。等待无模型请求，未完成当前块重建实现；已有 landed 块跳过。HTTP
`/api/tasks/{id}/answer`、任务卡及主 Agent task_status/task_answer 共用原答复服务和任务池。

新增后端 36 项开发问答回归，和主 Agent 答复一起定向 56 项通过。覆盖 prompt/native
真实主循环配假 LLM、实际临时 Git/测试、权限和计划变化、错归属与保存失败、清理前拒绝
答复、两次问答/三轮及手动恢复继承、重启不重放、blocked 投影和最终写盘失败。
旧并发计划归属测试的工具夹具更新为接受新增注入参数，仍断言无会话旧任务不装配问答。
最终 `pytest -q` 全部已收集范围 3401 项通过、11 项跳过、11 项既有弃用告警；该命令
范围比前轮 unit/integration 定向集合更广，计数不能全部当作新增开发问答测试。
前端 185 项通过；Desktop build、sidecar 构建、Ruff 和 git diff --check 通过。
Rust 代码未修改，本轮未重复 Rust 检查。

最新打包 runtime 使用临时 Git 仓库、回环确定性假模型、真实 HTTP 与 WebSocket 验证：

- 开发 Agent 写入部分临时文件后提问，同批后续写入未执行；blocked 时原槽释放，分支
  尚未提交、工作树清理，无主 Agent 自动调用。
- 用户回合中主 Agent 先读 task_status，再调用 task_answer；答复保存后原任务第 2 轮
  实现当前块并完成集成。主 Agent 请求 3 次，开发 Agent 总计 3 次；同答复 HTTP 重试
  不增加请求，已 landed 块 attempts=0，目标块 attempts=1，实际集成 2 项测试通过。
- 实际测试使用 macOS Seatbelt：policy=auto、isolated=true、fallback=false。
  完成回执独立，原 main/工作区不变，仅临时分支的 app.py 与测试变更，工作树清理。
- 原服务研究打包问答仍通过：主 Agent 请求 3 次、子 Agent 请求 2 次，最终
  done/round=2/review=pending，同答复重放不执行，自动检查仍关闭。

浏览器通过 ego-browser 对真实 TaskCard/TaskQuestionActions 和临时源码 API 做交互验收，
连接使用临时 fetch 适配、开发执行配假 LLM，未模拟原生 Tauri 权限或系统对话框：
建议答案可编辑；写盘失败保留草稿且不排队；服务保存成功后模拟丢失响应，重试同一答案
不会超过原三次开发模型请求；完成显示独立交接处理，记录回执后仍为 done。
过期问题隐藏回答表单，取消关闭问题且无额外模型调用；346 像素任务列没有横向溢出。
截图发现任务卡深色背景继承浅色主题深字色，已为任务卡明确正文颜色并复查。

证据：/tmp/vorto-dev-questions-final-focused.log、/tmp/vorto-dev-questions-full-pytest.log、
/tmp/vorto-dev-questions-frontend.log、/tmp/vorto-dev-questions-desktop-build.log、
/tmp/vorto-dev-questions-sidecar-build.log、/tmp/vorto-dev-questions-packaged-loop.json、
/tmp/vorto-dev-questions-research-packaged.log、/tmp/vorto-dev-question-browser-flow.json、
/tmp/vorto-dev-question-browser-layout.json、/tmp/vorto-dev-question-narrow-column.png。
临时浏览器页面、仓库夹具与服务在验收后清理；项目仓库未提交。

未调用真实模型，未安装或发布应用，未做打包原生 Desktop 点击验收。打包链路使用提示式
工具协议，native 回灌由源码假 LLM 回归验证。问答开发块当前串行；CLI/IM 独立开发、
模型回合内委派暂停、并行问答协调与依赖任务执行合同尚未开放。问答限制不是整个计划的
费用硬预算；跨进程及 Git/计划跨资源原子事务仍不保证，生产自动检查仍关闭。

### 只读研究依赖、固定结果消费与显式推进（2026-10-04）

本切片在原服务下派合同增加 depends_on，初次保存 waiting 和依赖合同；前置指定轮次
完成并验收后，通过 HTTP、任务卡或 Web/Desktop 主 Agent task_release 核对并推进。
waiting 不占共享槽，不保留协程；结果消费先落盘，再进入原 TaskRunner，实际模型启动
前重新核对版本与已消费祖先。前置失败在显式推进时保存下游 failed，保持原错误证据；
后来修复前置也不会把已失败合同变成可再次推进，必须新建合同。

新增依赖回归文件有 43 项，覆盖多前置、等待与验收、输入长度/完整版本、污点、请求与
推进幂等、失败传播、归属/角色/容量/存储失败、取消、重启、精确轮次和图边界。删除依赖
记录或把状态改为错误类型都不能绕过模型启动检查，任务卡投影显示损坏原因。真实
MainAgent 配假 LLM 验证 prompt/native 主 Agent 推进、不等待后台、动态 owner、消费后
提问与原任务答复；后端投影、协作、收件箱与服务器定向集合 99 项通过。

最终完整 `pytest -q` 为 3444 passed、11 skipped、11 warnings（205.51 秒）；既有跳过项
与警告保留。Desktop 前端测试 191 项通过，TypeScript/Vite 与最终冻结 runtime 构建通过；
Ruff 和 git diff --check 通过。前置选择的范围/轮次校验、空依赖与消费前失败的返工入口保持兼容。
Vite 保留既有大 chunk 警告，没有把构建通过当作原生 Desktop GUI 验收。

最终打包 runtime 在临时工作区、回环 HTTP/WebSocket 和确定性假 provider 中验收：

- 提交前置并完成后，后续任务保持 waiting，无新增模型请求；未验收推进返回 409。
- 通过真实 API 验收前置，主 Agent 使用 task_status/task_release 推进原任务，读取到
  固定前置 ID、轮次与完整 revision 的结果上下文；执行后仍 review=pending。
- 主 Agent 请求 3 次，研究 Agent 总计 2 次；重复 release 返回已完成状态，不增加调用。
- 消费不自动验收后续结果，也不处理前置交接回执，生产自动检查继续关闭。

浏览器通过 ego-browser 验收真实 TaskDispatchForm/useTaskDispatch/TaskCard 与源码 HTTP
服务。使用真实 GatewayClient 公共方法，只替换原生 HTTP 为浏览器 fetch；研究执行配假
LLM。界面选择前置后持久等待；未验收拒绝，任务卡记录验收后可推进。模拟保存失败时
保持 waiting、调用数不变；模拟响应丢失后重复点击返回实际完成状态，总研究请求仍为两次。
前置轮次变化拒绝旧选择且保留草稿；取消等待不影响上游、不增加调用；请求前置返工后，
显式推进只保存下游失败，卡片提示重新下派并隐藏不能使用的返工入口。

390px 验收视口内，任务列 clientWidth/scrollWidth 均为 346，任务卡均为 344，依赖区均为
322，无横向溢出；截图核对长 ID 与失败说明正常换行。临时前端源码、临时台账和本机
服务已清理，浏览器验收空间已关闭，项目仓库未提交。

证据：/tmp/vorto-dependencies-focused.log、/tmp/vorto-dependencies-final-focused.log、
/tmp/vorto-dependencies-full.log、/tmp/vorto-dependencies-frontend.log、
/tmp/vorto-dependencies-desktop-build.log、/tmp/vorto-dependencies-sidecar.log、
/tmp/vorto-dependencies-packaged-loop.json、/tmp/vorto-dependencies-packaged-runtime.log、
/tmp/vorto-dependencies-browser-qa.json、/tmp/vorto-dependencies-narrow.png、
/tmp/vorto-dependencies-narrow-top.png。

未调用真实模型、未安装/发布应用、未做打包原生 Desktop 点击验收。当前只接受同会话的
只读服务研究依赖，失败传播和推进需要显式动作；没有自动模型调度、开发/研究混合依赖或
整条任务链累计 token/费用硬预算。单进程原子文件不是跨进程 exactly-once；Git/计划
跨资源原子事务和模型回合内委派暂停仍未实现。PRD 第 25 节定义了当前边界与后续预算工作。

### 任务链累计执行额度与继承入口（2026-10-04）

本切片增加可选 chain_limits：新根记录累计任务名额与逐轮执行上限的预留，依赖任务
继承同一根，不能重设或合并不同额度根。waiting 只占任务数，不占执行槽或执行轮次。
推进、返工和回答先保存完整原单轮步骤/超时预留，再保存排队状态；后一步失败保留
保守预留，同合同重试复用。取消、失败、早结束及重启不返还历史额度。

新增 tests/unit/test_task_chain_budget.py 共 31 项，覆盖四维度、继承/合并/重设、同 ID
重试、跨记录保存失败、并发准入、问答与重启、缺失/损坏合同、严格 HTTP 字段和真实
MainAgent prompt/native 问答。预算关联与镜像同时丢失但前置仍带预算时也拒绝执行。
完整 pytest -q 为 3475 passed、11 skipped、11 warnings（211.63 秒），Desktop 前端
196 项通过；TypeScript/Vite 和最终冻结 runtime 构建通过。Ruff、git diff --check 通过。
Vite 保留原大 chunk 警告。

TaskFeed 收到带额度的任务更新/交接事件后重读权威任务快照，同链其他卡片也能刷新
共享额度；不从单个子任务计算其他任务的预留。新增两项测试验证逆序响应不覆盖较新
链快照，以及连接切换后拒绝旧额度事件触发的迟到读取。

最终打包 runtime 在临时工作区与回环 HTTP/WebSocket 中配确定性假 provider 验收：

- 根第一轮完成后，子任务 waiting 只增加任务数；前置未验收 release 返回 409。
- 真实 API 验收前置后，主 Agent task_status/task_release 推进同 ID 子任务，研究
  Agent 读取固定前置完整 revision 的上下文。根累计预留为 tasks=3、rounds=2、steps=6、
  timeout_seconds=60，子任务执行结束仍待验收。
- 主 Agent 请求 3 次、研究 Agent 请求 2 次。重复 release 不执行；额度耗尽的 followup
  与另一个 waiting 子任务 release 返回 409，模型请求数不变，取消不退款。
- 结果消费、验收与交接处理回执继续分开，token_cost_hard_limit=false。

ego-browser 在真实 TaskDispatchForm/useTaskDispatch/TaskCard 与源码 HTTP 服务中验收。
GatewayClient 使用公共方法，仅将原生 HTTP 传输替换为浏览器 fetch，研究执行配假 LLM。
为了实际触发拒绝，夹具把默认额度缩小为 3 项任务、2 轮、16 步配额、600 秒超时配额；
产品默认仍为 8/12/64/1800。界面验证：

- 启用根额度提交并模拟响应丢失；保留同一 request_id 重试，一项任务、一轮预留，
  没有重复执行。成功后清空草稿。
- 选择已验收的带额度前置后，继承控件选中且不可重设；请求不发送新的 chain_limits。
  子任务等待期间总轮次仍为 1，显式推进后变为 2。
- 额度耗尽拒绝补充执行，旧轮次仍为 1，补充说明保留；拒绝另一等待任务推进时仍
  waiting。取消后 tasks 仍为 3，累计轮次仍为 2。
- 达到任务名额上限后拒绝创建新任务，表单目标草稿保留；整个研究模型请求总数仍为 2。

390px 视口内任务列 clientWidth/scrollWidth 均为 346，三张任务卡均为 344，三处预算
区域均为 322，无横向溢出；截图核对额度继承、拒绝说明与用量正常换行。
浏览器验收空间已关闭，两个本机服务、临时台账和临时前端源码已清理，仓库未提交。

证据：/tmp/vorto-chain-focused.log、/tmp/vorto-chain-full.log、/tmp/vorto-chain-frontend.log、
/tmp/vorto-chain-desktop-build.log、/tmp/vorto-chain-sidecar.log、
/tmp/vorto-chain-packaged-loop.json、/tmp/vorto-chain-packaged-runtime.log、
/tmp/vorto-chain-browser-qa.json、/tmp/vorto-chain-narrow.png。

执行额度按原合同预留，不是实际 token、费用、provider 请求数或真实耗时计量；强制收尾、
压缩、工具批次和 provider 隐式请求不由主循环步骤配额硬封顶。当前没有真实模型调用、
应用安装/发布或原生 Desktop GUI 点击验收，生产自动检查保持关闭。单进程文件及共享锁
不保证跨进程事务，也不承诺抵御整个台账的恶意篡改或回滚。下一阶段先处理执行期间
前置失效与停止协调，再扩展自动推进；合同和范围见 PRD 第 26 节。

### 执行期间依赖失效、历史结果与显式停止（2026-10-04）

本切片在原依赖合同保存固定 invalidated marker，保留原消费快照。只读研究装配
execution_check；主循环只调用检查回调，不引入任务领域依赖。prompt/native 模型
请求、压缩、流式正文、工具准入/返回和最终协作提交均核对 consumed 版本。
失效不能提交成功，前置恢复原值也不解除旧合同失效；历史 done/result/review 分开保留。

新增 tests/unit/test_dependency_invalidation.py 共 32 项：实际 Agent 的模型/工具在途
变更、prompt/native 路径、压缩及 provider 错误降级、流式正文、异步 hook 后工具准入、
最终提交门、首时隙前停止、吞掉取消、重复停止与共享槽清理、失效记录与终态保存失败、
严格 HTTP、动态 owner 主 Agent 工具、问答关闭、损坏记录、传递失效和重启。
终态保存失败时保留停止未确认，存储恢复后的核对只补记终态，不重新调用模型。
既有依赖/预算/协作/问答/下派集合 124 项通过；Desktop 前端 199 项通过。

最终完整 pytest -q：3507 passed、11 skipped、11 warnings（211.53 秒）。既有跳过与
警告保留，最终代码包含停止未确认和终态补记边界；没有把假模型验收当成真实费用证明。

最终 TypeScript/Vite 与冻结 runtime 构建通过，Ruff 和 git diff --check 通过；Vite 保留
原大 chunk 警告。新 task_reconcile 已登记为 Web/Desktop 服务工具及中文时间线标题，
CLI/TUI 未注入共享服务时不暴露该工具。

最终冻结 runtime 在临时工作区、回环 HTTP/WebSocket 和确定性假 provider 下验收：

- 前置结果已验收，子任务显式推进后开始请求；在途期间改动台账里的前置结果。
  Web 主 Agent 用真实 task_status/task_reconcile 记录失效并请求原 TaskRunner 停止。
- 子任务最后为 failed，无迟到成功结果；原输入快照与累计额度预留保持不变。
  重复 release/reconcile 不执行，失效合同 followup 返回 409。
- 已完成且验收、处理过的另一个子任务失效后保留 done/result/accepted，生成新的未
  处理 revision；恢复前置原值后仍失效。主 Agent 请求 3 次，两个根/两个子任务共 4 次
  研究请求，夹具断言错误为 0。

ego-browser 验收真实 TaskCard/TaskDependencyActions/TaskQuestionActions 与源码 HTTP
服务；使用 GatewayClient 公共方法，只替换原生 HTTP 传输为浏览器 fetch。前置通过
夹具写入台账模拟版本变化，研究执行使用真实 MainAgent 配假 LLM：

- 失效保存返回 503 时仍 running，未请求取消，模型调用数为 1。
- 保存恢复后模拟核对响应丢失；相同任务/轮次重试，失效消息只有一次，清理仍占原槽。
  任务卡显示停止尚未确认，允许刷新或重新核对，未提前显示已停止。
- 允许夹具清理返回后最终 failed，无结果；轮次与步骤/超时预留不退款。
- 历史验收和旧结果仍可查看，卡片单独标明失效，隐藏验收/返工入口；旧交接回执不
  覆盖新失效 revision。有效前置需重新选择后下派，不自动恢复旧任务。
- blocked 的失效问题不再展示回答入口；核对后关闭问题、保持原轮次和累计预留，
  模型调用数不增加。

最终停止提示在重新启动的源码夹具中复核，390px 视口内任务列 clientWidth/scrollWidth
均为 346，任务卡均为 344，依赖区为 322；截图确认停止未确认、原因和任务额度正常
换行，无横向溢出。临时台账、两个本机服务和临时前端源码已清理，浏览器空间已关闭。

证据：/tmp/vorto-invalidation-existing.log、/tmp/vorto-invalidation-focused-final.log、
/tmp/vorto-invalidation-full-final.log、/tmp/vorto-invalidation-frontend.log、
/tmp/vorto-invalidation-desktop-build.log、/tmp/vorto-invalidation-sidecar.log、
/tmp/vorto-invalidation-packaged-loop.json、/tmp/vorto-invalidation-packaged-runtime.log、
/tmp/vorto-invalidation-browser-qa.json、/tmp/vorto-invalidation-stopping.png。

这是执行检查点与显式停止切片，没有反向依赖索引、变更瞬间全图自动停止或自动推进。
取消本机 await 不证明 provider 远端立即结束或停止计费；同步工具/清理仍需返回。
未调用真实模型、未安装/发布、未做原生 Desktop GUI 点击验收，也未提交仓库；生产
自动检查继续关闭，单进程台账不提供跨进程事务保证。后续范围见 PRD 第 27 节。

### 依赖事件刷新遗漏与流式中断传播（2026-10-04）

先以新增用例复现问题：TaskFeed 新增的 3 个基础场景全部失败；SDK 形状的离线流与
生成器收尾 11 个基础场景全部失败。修复覆盖无预算前置更新、旧快照被无关事件打断
后补读，以及流式检查异常被展示隔离吞掉、推理片段未检查和本地流未关闭。

新增 tests/unit/test_stream_invalidation.py 共 17 项，使用真实 MainAgent/LLMClient 配
离线 SDK 形状响应：prompt/native × 正文/推理 × 有/无推理观察者；失效后只读到第二
片段、仅展示第一片段，不读取第三片段，关闭一次且只请求一次。另覆盖普通展示异常
仍继续生成、异步生成器关闭、取消/SDK 读取失败、关闭失败保留原持久化错误并告警。
此集合与既有失效、用量和主循环共 204 项通过。TaskFeed 16 项通过，含 100 条事件
突发的合并、无关事件补读、旧连接/重置、完整快照中止待补读和读取失败无自动重试。

完整 pytest -q：3524 passed、11 skipped、11 warnings（210.52 秒）。前端 206 项通过，
TypeScript/Vite 构建通过（保留既有大 chunk 警告）；Ruff 与 git diff --check 通过。
managed Python 3.13 冻结 runtime 构建通过，产物为 21,166,528 字节。新 runtime 使用
原回环假 provider 复核停止与历史交接：主 Agent 请求 3 次、研究请求 4 次、断言错误 0，
原输入/额度保持、迟到结果拒绝、历史 done/result/accepted 保留且旧回执不吞失效通知。

ego-browser TaskSpace 71 使用现有 TaskFeed/TaskCard/GatewayClient 公共方法；原生 HTTP
传输换为浏览器 fetch，WebSocket 接入现有任务广播桥，服务器使用临时台账和假 LLM：

- 无链预算的前置验收事件自动把 waiting.ready 更新为 true，无手动刷新或自动执行。
- 显式推进后子任务 done/pending；夹具改变前置证据并只发前置事件，下游卡自动展示
  “依赖已失效 · 历史记录保留”，保留结果并隐藏验收、返工与追加轮次入口。
- 100 条真实 WebSocket 前置事件触发 2 次快照读取，最大同时在途为 1。
- 显式核对后保存 invalidated，仍保留历史 done；假模型只调用 1 次，无自动重跑。

浏览器空间已关闭；两个本地服务、临时台账及前端夹具源码已清理。证据保存在
/tmp/vorto-event-browser-qa.json、/tmp/vorto-feed-red.log、/tmp/vorto-feed-focused.log、
/tmp/vorto-stream-red.log、/tmp/vorto-stream-focused-final.log、/tmp/vorto-event-stream-full.log、
/tmp/vorto-event-stream-frontend.log、/tmp/vorto-event-stream-build.log、
/tmp/vorto-event-stream-sidecar.log、/tmp/vorto-event-stream-packaged.log。

这是源代码、本地离线流、冻结 runtime 假 provider 与浏览器组件验收；未验证真实
provider 的流关闭/计费效果或原生 Desktop GUI。当前 shell 没有可选 openai SDK，
SDK 流路径使用离线形状响应验证，未获得真实 SDK 的 SSE 网络验收证据。
服务端反向发现、全图自动停止尚未
实现，客户端事件刷新不替代它们；未安装、发布或提交，生产自动检查继续关闭。

### 有界反向发现、服务事件与启动/用户回合核对（2026-10-04）

本切片在原 TaskLedger/TASK_STATE_LOCK 上临时发现下游，复用原 DispatchService 的失效
持久化与 TaskRunner 停止/清理路径。服务协作通知、主 Agent 审查适配和执行池更新均接入，
不依赖活跃客户端；启动恢复与已有所属用户回合补查离线历史变化。没有新增台账、索引、
执行池、文件监听或轮询，不自动 release、回答、确认交接或调用模型。

GET /api/delegations/{id}/dependents 只读发现，POST reconcile-dependents 逐项核对，固定
来源 owner/精确轮次，公开范围限定同 owner。每次最多扫描 512 个目录项、每条 262144 字节，
下游 32 个、8 层、128 次边访问。超界、跳过、不可读、合同损坏和保存失败明确报告
complete=false；202 或 complete=true 均不表示执行器清理已结束。

新增 tests/unit/test_task_dependents.py 共 40 项，覆盖：

- 已验收/已处理历史在来源变化后保存 marker、产生新未读 revision，重放不重复消息；
  原 done/result/review、inputs 和累计额度保留，来源恢复也不解除失效。
- 服务事件发现结果、验收、归属、轮次和缺失变化；等待仅通知投影，不执行或落盘；
  无客户端的在途停止保留清理槽，重复取消不打断清理，迟到成功不能提交。
- 首时隙取消、blocked 问题关闭、传递历史失效、marker 保存失败不请求取消、终态保存
  失败保持 stop_pending，存储恢复后仅补终态；通知传输失败仍继续领域核对。
- 启动/所属用户回合补查、其他 owner 不变、工作区不一致保持覆盖未知；没有消费合同
  的回合不构造后台池，普通已处理完成任务不产生额外提示。
- 真实 HTTP 的鉴权、只读不改台账、来源归属/轮次和严格 POST 字段、批量重试，以及
  扫描/记录大小/符号链接/不可读目录、图深度/扇出/边访问、损坏身份的覆盖报告。
- 最后以过深但小于字节上限的 JSON 复现未捕获 RecursionError；现改为跳过并报告
  覆盖不完整，保留原文件和其他有效记录。台账与新增依赖集合 67 项通过。

完整 pytest -q：3564 passed、11 skipped、11 warnings（194.47 秒）。Desktop 前端 206 项
通过，TypeScript/Vite 构建通过（20.80 秒，保留既有大 chunk 警告）。Ruff 与
git diff --check 通过。最终 managed Python 3.13.13 冻结 runtime 构建通过，产物
21,178,880 字节；不是安装包发布或原生 GUI 验收。

最终冻结 runtime 在临时台账运行，provider 为本机确定性假 HTTP 服务，停止/重启期间
没有 WebSocket 客户端：

- 只读下游查询不保存 marker；显式批量核对保存失效并请求原池停止，最终 failed/无结果。
  原 inputs 和链额度保持，重试不再次保存 marker 或执行模型。
- 两级历史下游 done/accepted/已处理后，夹具直接改变来源文件；重启核对保存两个
  invalidated marker，保留历史结果/审查，两个旧回执对应的新 revision 都重新未读。
  启动没有模型调用，恢复来源原值仍不解除失效。
- 随后显式发送所属用户回合，真实主 Agent 通过 task_inbox 读到两项历史失效通知；
  研究请求累计 5 次，主 Agent 请求 2 次，断言错误 0，无额外自动请求。
- 冻结 runtime 遇到过深 JSON 时，发现接口正常返回 complete=false/skipped=1，未改
  损坏文件或发起模型请求。临时台账、runtime 子进程及假 provider 均已清理。

证据：/tmp/vorto-dependents-focused-final.log、/tmp/vorto-dependents-audit-final.log、
/tmp/vorto-dependents-deep-json-red.log、/tmp/vorto-dependents-scan-final.log、
/tmp/vorto-dependents-full-verified-final.log、/tmp/vorto-dependents-frontend.log、
/tmp/vorto-dependents-build.log、/tmp/vorto-dependents-sidecar-verified-final.log、
/tmp/vorto-dependents-packaged-smoke-final.log、/tmp/vorto-dependents-packaged-smoke.json、
/tmp/vorto-dependents-packaged-runtime.log。

本切片补服务端和能力类型，沿用既有任务卡；未新增浏览器/原生 GUI 点击证据，未调用
真实 provider、安装、发布或提交。目录扫描没有稳定分页，大台账可能保留未覆盖任务；
直接文件变更没有事件，需等检查点、启动、已有所属用户回合或显式核对。不承诺全图
瞬时停止、远端计费停止或跨进程事务。生产自动检查仍关闭，后续范围见 PRD 第 29 节。

### 大台账稳定分页与累计覆盖证据（2026-10-04）

本轮承接第 29 节的未分页边界，在原 TaskLedger 下新增 task_scans 检查点，沿用
TASK_STATE_LOCK、DispatchService、依赖协调和原执行池。固定工作区、owner、来源 ID/轮次、
request_id、mode 和 page_size；清单按文件名 UTF-8 字节升序。读取完全部有界清单后才
规划下游，再分批核对。每页完成、清单完成、图完成和全部候选核对完成分别记录。

源码合同新增 tests/unit/test_task_coverage.py 共 43 项，包含超过旧 512 项扫描前缀的实际
漏查复现、70 个下游的 32/32/6 分批、分页期间增删改/owner/round/恢复 mtime、来源身份
变化、损坏和符号链接、读取/清单/图/页预算、单页重放、重启恢复、同页副作用、marker
与检查点保存失败，以及累计摘要链独立复核。旧结果、历史 done/accepted、原消费输入和
额度不覆盖；旧交接回执不吞新失效 revision。损坏或不完整清单不进入批量核对。

最终完整 pytest -q：3607 passed、11 skipped、11 warnings（203.26 秒）。新增覆盖与既有
台账/失效/下游集合 185 项通过；覆盖与生成态忽略规则集合 61 项通过。最终前端 21 个
文件共 218 项测试通过，含 12 项覆盖响应合同测试。TypeScript/Vite 构建通过（20.40 秒，
保留既有大 chunk 警告）；Ruff 与 git diff --check 通过。managed Python 3.13.13 冻结
runtime 构建成功，产物 21,199,152 字节。未改原生 Rust 层，未声称完成安装包验收。

最终冻结 runtime 先复制到独立临时目录，再在 671 条记录（600 条填充、来源及 70 个
历史 done/accepted 下游）上运行。GatewayClient 使用真实公共方法及原 request 实现；
ego-browser TaskSpace 79 加载真实 TaskCard/TaskCoverageActions，仅以浏览器 fetch 代理
替换原生 HTTP 插件。服务端为冻结运行包，无领域响应 mock；本机 provider 请求陷阱
统计意外请求，未使用真实模型或账户。实际客户端验证：

- 第一页读取 512/671，明确 incomplete，并显示下游范围尚未完整确认；完成记录读取
  后仍是下游 0/70 未完成，分批变为 32/70、64/70、70/70，仅最后一页 complete。
- 丢弃第二页响应后界面显示 unknown，保留原游标；相同游标重试 replayed=true，序号、
  receipt_count 和摘要保持不变，不重复推进或保存 marker。
- 修改已完成清单中的记录，检查覆盖有效性后旧扫描变为 unknown，保留历史计数和
  证据；新请求重新读取清单，不将不同快照的范围累计。
- 新扫描第一页后重启 runtime，重新加载客户端并显式恢复，仍是同一检查点的
  512/671；未自动推进分页。挂起响应期间断开客户端连接，迟到结果未提交到新连接；
  重新加载并恢复后可继续完成原检查点。
- 最终 70 个失效 marker、70 个新未读 revision，原 done/result/accepted 和消费输入均
  保留，每个下游 marker 消息最多 1 条。runtime 启动 2 次，模型请求总数为 0。
- 从实际检查点独立重算 5 条收据的摘要链，与客户端 evidence_digest 匹配；确认前缀
  为记录 [0,671)、下游 [0,70)，检查点 86,146 字节，生成态忽略规则含 task_scans/。

截图检查 390px 任务卡区域：body=390、card=356、coverage=334，无横向溢出。夹具仅为
测试狭窄任务区域覆盖 html/body/root 的 min-width；产品 Desktop 原有 900px 最小宽度
保留，所以这不是整套原生 Desktop 在 390px 下的验收。

首次夹具曾直接运行仓库的 runtime 文件，同时重建该文件，导致延迟解压失败；该次运行
不计入成功验收。改为独立副本后从新临时台账重跑上述全部路径，完成证据来自该副本。
浏览器空间已关闭，本轮两个本机服务、临时台账及四个前端 QA 文件已清理；其它会话
未处理，无关工作区改动保留，未提交仓库。

证据：/tmp/vorto-coverage-domain-final.log、/tmp/vorto-coverage-state-final.log、
/tmp/vorto-coverage-full-final.log、/tmp/vorto-coverage-frontend-final.log、
/tmp/vorto-coverage-desktop-final.log、/tmp/vorto-coverage-sidecar-final.log、
/tmp/vorto-coverage-client-qa.json、/tmp/vorto-coverage-client-complete.json、
/tmp/vorto-coverage-client-390.png、/tmp/vorto-coverage-packaged-runtime.log、
/tmp/vorto-coverage-qa-controller-final.log。

以上分别是源码测试、本地构建、冻结运行包 HTTP 和真实客户端组件验收；原生 Desktop
GUI、真实 provider 的硬预算/远端停止与计费、实际生产环境仍未验收，未安装或发布。
complete 仅表示固定来源与同 owner 范围在报告观察时刻覆盖完整，不表示全工作区所有
owner 的图、结果验收、交接处理、持久交付或 Goal 完成。清单超 8192 项、扫描正文超
64 MiB、图超 1024 个下游/8192 次边/8 层、推进超 1024 页均保留 incomplete/unknown；
32 个检查点达到保留上限拒绝新扫描，不静默删除历史。没有跨进程快照或恶意回滚保证，
直接文件变化仍需显式继续/检查才能观察。生产自动检查和自动推进保持既有关闭边界。
后续优先处理凭据保留/归档和外部变更观察，合同及范围见 PRD 第 30 节和 TASK_DISPATCH_API.md。

### 覆盖凭据保留、原文导出与显式容量释放（2026-10-04）

承接第 30 节，原 TaskLedger/task_scan.py 和 TASK_STATE_LOCK 增加有限历史封套存储，
DispatchService/HTTP 为薄适配；原 TaskCoverageActions 内提供资料管理。不增加任务数据库、
执行池、后台归档或自动推进。活动仍限 32 个，历史限 128 个，每个 8454144 字节；
历史文件不可覆盖，归档与释放为两个显式动作，旧轮次和失效证据保留。

新增 tests/unit/test_task_coverage_archive.py 共 31 项，覆盖真实 32 个活动容量与 128 个
历史容量、分页资料、归档后容量不变、保留后释放、重复操作/重启、旧请求不可重建、
活动观察/版本变化、owner/source/round/workspace 隔离、来源删除/变更、读取/写入/替换/
同步/回读/删除失败、删除后同步失败重试、损坏/校验和/收据链/范围/符号链接/超长资料，
以及严格 HTTP 鉴权/字段、导出只读核验。最终相关合同/原分页/下游/路由集合 133 项
通过（6.32 秒），归档集合 31 项通过（含实际 128 个历史文件，无静默淘汰）。

首次完整 pytest 在沙箱内有 17 项既有本机服务器用例因禁止绑定端口失败，记录保留于
/tmp/vorto-retention-full.log；获得本机端口测试执行权限后完整复跑：3638 passed、
11 skipped、11 warnings（197.10 秒），日志 /tmp/vorto-retention-full-final.log。
最终前端 22 个文件共 230 项通过，新增 11 项历史合同和 1 项 64 位 JSON 导出回归。
TypeScript/Vite 构建通过（20.62 秒，既有大 chunk 警告保留）；Ruff/git diff --check
通过。managed Python 3.13.13 冻结 runtime 为 21,211,456 字节，未改原生 Rust 层。

冻结 runtime 复制到独立临时目录，再以真实 HTTP 预填 32 个活动检查点。每个对 671 条
任务记录（600 个填充、来源和 70 个历史下游）执行原分页合同；临时 provider 仅为本机
请求陷阱。ego-browser TaskSpace 80 使用原 TaskCard、TaskCoverageActions、
TaskCoverageRecords 和 GatewayClient 公共方法，只将原生 HTTP 插件替换为浏览器 fetch
代理，无领域响应 mock。实际运行包与客户端操作验证：

- 32 个活动容量已满时拒绝新扫描，界面给出加载资料、先保留历史再显式释放的路径；
  归档响应丢失后同 request_id 重试，replayed=true、归档 ID/摘要/时间不变。
- 归档本身不释放活动容量。客户端归档/释放后读取权威容量，其他时刻显示“上次加载
  的容量”；活动列表有独立滚动区域，历史区域不注入当前覆盖。
- 实际导出下载发现 JSON.parse/stringify 会舍入 lstat 纳秒整数，独立摘要核验失败。
  修复为 requestText/原始 JSON Blob，重新下载 80,714 字节，通过独立标准库 SHA-256
  重算封套、原检查点及全部 5 条收据链；旧失败不计入通过。
- 显式释放响应丢失后相同归档/版本重试，replayed=true；活动从 32 变为 31，历史仍为
  1。旧扫描 request_id 返回 409，不能重建；新请求可读取 512/671，仍 incomplete。
- 另一次归档响应挂起期间断开连接，迟到回执未提交；历史已保存在服务器。重启并
  重新加载后显式恢复原 512/671 分页，并能看到两份固定身份的历史资料。
- 后续改变记录并检查，当前覆盖变为 unknown；旧历史仍显示当时 complete，恒为
  historical=true/current_coverage=false/resumable=false。导出封套按原文 POST 核验通过。
- 夹具损坏第二份归档，释放接口返回 503，活动仍为 32、历史仍为 2；客户端明确显示
  损坏历史 unknown，不能用它释放容量，未删除坏文件或旧历史。
- runtime 启动 2 次，模型请求总数为 0；70 个旧 done/result/accepted 与消费输入保留，
  70 个失效 marker/新未读 revision，每个 marker 消息最多 1 条，无任务重跑或交接确认。

初始浏览器导出操作曾因夹具沿用产品 body 隐藏溢出、缺少产品的滚动面板而未点击到
屏幕外按钮；修正夹具滚动容器后再验收，不将该尝试计为成功。最终 390px 狭窄任务区域
body=390、card=364、records=322，无横向溢出，截图核对当前 unknown、历史 complete 和
损坏历史 unknown 分开呈现。夹具仅覆盖 min-width/滚动布局，产品 900px 最小宽度保留；
这是任务区域验证，不是整套原生 Desktop 的窄屏验收。

浏览器空间已关闭；仅停止本轮临时配置的 Vite、控制器及当前运行副本，三个测试端口
均关闭，临时台账及四个 QA 源文件已清理。Codex 桌面端、协调聊天、其它项目进程与
无关工作区改动未处理，仓库未提交。导出原文及运行证据保存在：
/tmp/vorto-retention-archive-final.log、/tmp/vorto-retention-contracts-final.log、
/tmp/vorto-retention-full-final.log、/tmp/vorto-retention-frontend-final.log、
/tmp/vorto-retention-desktop-final.log、/tmp/vorto-retention-sidecar.log、
/tmp/vorto-retention-client-qa.json、/tmp/vorto-retention-export.json、
/tmp/vorto-retention-independent-verify.json、/tmp/vorto-retention-client-390.png、
/tmp/vorto-retention-packaged-runtime.log、/tmp/vorto-retention-controller.log。

按用户最新指令暂缓原生应用、窗口/焦点、输入法、Spaces、VoiceOver 与真实 Hook 验收；
此前未验收/失败记录保留，不改记通过。本轮没有启动原生 Desktop GUI 验收，没有真实
provider 硬预算/计费/远端停止、生产环境、安装或正式发布证据。归档与核验不证明任务
停止、验收、交接处理、持久交付或 Goal 达成；共享锁与文件同步不提供跨进程事务或
恶意回滚保证。历史空间满时保留资料并拒绝新增，没有历史删除或导入；长期迁移与
外部变更观察留待下一阶段，自动检查/自动推进/provider 权限保持原边界。
详细合同见 PRD 第 31 节和 TASK_DISPATCH_API.md。


### 活动覆盖的显式外部变化观察（2026-10-04，PRD 第 32 节）

承接第 31 节的已完成记录。本轮复现资料列表在外部台账修改后仍返回上次 complete，
保留该历史语义，同时增加原任务卡内的显式所选观察。实现位于原 TaskLedger/task_scan、
共享锁和 DispatchService，HTTP/GatewayClient 为薄适配；不增加任务库、执行池、监听器、
轮询、模型检查或自动推进。每次 1 至 4 项、16 MiB 活动原文字节（含损坏读取，最多
一个超限检测字节）、两次各 8192 项目录观察，固定工作区/归属/轮次/检查点摘要/快照/序号。

源码合同测试新增 tests/unit/test_task_coverage_observation.py，最终 25 项通过（2.16 秒）：
外部增删改/来源删除/owner 或轮次变化/损坏、已失效恢复不解除、稳定 incomplete 游标
和收据保留、重启、丢失响应/旧版本重试、所选四项与未选范围区分、历史原文保留及旧
归档不能释放新活动版本、观察期间目录变化/超限/不可读、单项损坏/缺失/保存失败与其它
分支继续、真实 16 MiB 多检查点预算（正常与损坏读取都占预算）、跨归属/来源/轮次/
工作区拒绝、重复/空/超量范围、严格 HTTP 字段/鉴权/General 目录门、自动能力未开放。
最终相关合同/原归档/分页/下游/失效/路由集合 190 passed、7 warnings（8.68 秒）。

完整后端回归 3662 passed、11 skipped、11 warnings（213.45 秒）；此后仅补充一项损坏
读取预算参数用例及 General 断言，对最终 25 项和上述 190 项集合复跑通过，不将完整
回归条数增加为未实际运行的数字。前端 23 个文件 246 项通过，新增 16 项固定响应/
原版本/快照/序号/摘要链/未推进计数/未确认分支与 discovery 语义校验。TypeScript/Vite
最终构建通过（21.38 秒，既有大 chunk 警告保留）；Ruff/git diff --check 通过。
managed CPython 3.13.13 冻结 runtime 为 21,215,712 字节，原生 Rust 层未改。

独立临时运行包复制原冻结 binary，临时 TaskLedger 含 671 条任务（600 填充、来源和
70 条历史 done/accepted/已消费下游）。通过真实 HTTP 建立 5 项完整和 1 项 512/671
不完整覆盖。ego-browser TaskSpace 86 使用原 TaskCard/TaskCoverageActions/Records 和
GatewayClient 公共方法，仅以 fetch 代理替代原生 HTTP 插件，无领域响应 mock。实际
客户端操作及独立运行副本验证：

- 显式加载六项活动资料并保留一份完整历史。默认选择前四项，未选第五项处于禁用，
  取消一项再选择第五项仍最多四项。观察四项稳定范围，512/671 仍 incomplete，原
  游标、序号、快照与累计摘要不变；完整历史保持 historical/current_coverage=false。
- 外部修改填充记录后显式观察，四项原快照均锁存 unknown，没有继续游标，当前
  已显示的 512/671 卡片同步 unknown；两项未选择资料继续明确显示上次 complete，
  不算已观察。pages_advanced=0、task_graph_reconciled=false，未核对任务图。
- 损坏一个所选活动文件，其余三个仍观察成功。界面显示“已观察 3，未确认 1”，
  损坏项 unknown，无 report/持久成功回执，不删除或重建坏文件；坏资料仍占活动容量。
- 观察响应在服务器保存后被传输夹具丢弃，客户端保持原版本并显示失败；旧摘要再
  试四项均 checkpoint_changed/unknown，不能猜先前成功。显式重载版本后可继续观察。
- 暂留另一份真实观察响应后断开连接，释放迟到响应没有提交资料或更新通知计数。
  服务器保存仍保留，重新连接后只能显式加载，不自动推进或补做模型工作。
- 重启 runtime 后显式加载，活动 6/32、历史 1/128、不可读活动 1 项；原 partial-scan
  仍序号 1、读取 512/671、失效锁存且无游标。未将损坏资料猜成可恢复扫描或删除。
- 原客户端实际导出下载 80,486 字节历史 JSON，与观察前原文逐字节一致；独立标准库
  重算证据/原检查点 SHA-256 和全部 5 条收据链通过，不调用产品核验函数。
- runtime 启动两次，模型请求总数为 0；70 条旧结果、done/accepted 和消费输入保留，
  未增加依赖失效 marker 或交接 revision，未停止、启动、验收或确认任何任务。

一次前端热更新重置了资料子组件状态，随后的观察按钮定位没有发生点击；该尝试保留
为未执行/不计通过，检查页面并显式重载资料后才完成丢失响应验收。最终 390px 任务
区域截图视觉检查：viewport=390、body=382、card=358、records=314，无横向溢出；活动
unknown、历史当时 complete、不可读活动占容量和“任务图未核对”分开呈现。夹具仅
调整页面最小宽度/滚动容器，产品 900px 最小宽度保留，不能计作原生 Desktop 窄屏验收。

浏览器空间已关闭；仅停止本轮精确命令的 Vite、控制器及其当前冻结运行副本，临时
台账和四个自建验收源文件已清理；三个本轮端口关闭。Codex 桌面、协调聊天、其它
项目进程及既有无关工作区改动保留。未提交、安装或正式发布。证据：
/tmp/vorto-observation-full.log、/tmp/vorto-observation-focused-final.log、
/tmp/vorto-observation-contracts-final.log、/tmp/vorto-observation-frontend-final.log、
/tmp/vorto-observation-desktop-final.log、/tmp/vorto-observation-sidecar.log、
/tmp/vorto-observation-client-mutation.json、/tmp/vorto-observation-client-loss.json、
/tmp/vorto-observation-client-late.json、/tmp/vorto-observation-client-restart.json、
/tmp/vorto-observation-client-final.json、/tmp/vorto-observation-history-before.json、
/tmp/vorto-observation-history-after.json、/tmp/vorto-observation-independent-verify.json、
/tmp/vorto-observation-client-390.png、/tmp/vorto-observation-packaged-runtime.log、
/tmp/vorto-observation-controller.log。

按用户指令继续暂缓原生应用/窗口/焦点、输入法、Spaces、VoiceOver 和真实 Hook；原生
此前失败/未验收记录未删除或改记通过。真实 provider 的 token/计费硬预算、远端停止、
生产环境、原生 GUI 与正式发布均未验收。共享锁和双次目录观察不提供跨进程事务、
连续变更监听、内容签名或电源故障保证。历史始终不是当前覆盖，观察只更新元数据，
不证明停止、验收、交接处理、持久交付或 Goal。下一阶段先设计历史满 128 项后的长期
迁移合同；本轮未开放历史删除/导入、自动推进/模型检查或新 provider 权限。
详细合同见 TASK_COLLABORATION_PRD.md 第 32 节及 TASK_DISPATCH_API.md。


### 长期迁移前的历史文件原文回验（2026-10-04，PRD 第 33 节）

承接第 32 节。本轮补齐历史满 128 项后迁移前的只读基础：原任务卡选择已下载的
JSON 原文回验，原 vc CLI 离线读取普通文件。实现共用 TaskLedger/task_scan、共享锁
和原 DispatchService/GatewayClient，不增加任务库、索引、执行池、监听、模型检查
或自动推进。HTTP 固定工作区/归属/来源/轮次/归档/原文摘要，原文与结构摘要分开，
8454144 字节流入预算；不经 JavaScript Number 重编码，不读取或写入原活动/历史文件。
文件回执不证明外部持久保存，不导入、恢复或释放容量；历史 128 上限未改变。

新增 tests/unit/test_task_coverage_file.py 最终 28 passed（1.07 秒），覆盖原文纳秒整数、
重复只读、来源/归属/轮次已变或本地归档损坏时副本独立核验、七项绑定、UTF-8/BOM/
重复字段/非有限数/截断/摘要/链损坏/预算、空白格式的双摘要、离线符号链接/目录/FIFO/
超长/读取期间路径变化、无台账 cwd 和配置日志不写入、HTTP 鉴权/General/严格查询/
媒体类型/声明及无长度流预算。实际复现 1e9999 被 Python 解析为 Infinity、内部摘要
重算后误判 verified；增加有限 parse_float 拒绝及针对该问题的回归，不记旧行为通过。
最终相关文件/归档/观察/分页/下游/失效/CLI/路由集合 228 passed、7 warnings（7.93 秒）。

完整后端回归 3690 passed、11 skipped、11 warnings（217.53 秒）。此后仅增加上述指数
溢出修复和用例，最终 28 项及 228 项集合复跑；不把未再次完整运行的数字改为 3691。
前端最终 24 个文件 272 passed（782 ms），相对第 32 节新增 26 项，含原文精度/SHA、
坏文件/超长、固定身份/快照/累计链/序号/归档请求和时间、非持久标志及原文 HTTP 正文。
TypeScript/Vite 最终构建通过（20.81 秒，既有大 chunk 警告保留），Ruff/git diff --check
通过。managed CPython 3.13.13 冻结侧车 21,219,696 字节，SHA-256 为
4d8b1edbd572b11e86dbe483f590273e901e3542ced2566acf04c2d8480103aa；原生 Rust 层未改。

独立临时目录逐字复制最终冻结 binary，671 条任务（600 填充、来源和 70 条历史下游），
通过真实 HTTP 建立五项 complete、一项 512/671 incomplete 和 128 份真实历史封套。
ego-browser TaskSpace 90 使用原 TaskCard/TaskCoverageRecords/TaskCoverageFileCheck 和
GatewayClient，只替换原生 HTTP 插件为浏览器 fetch 代理，领域响应不 mock。实际验收：

- 历史满 128/128 给出保留资料、只读导出和迁移待处理提示。实际再点归档返回明确
  容量错误；805 个任务/活动/历史文件 SHA-256 与纳秒 mtime 完全不变，无静默淘汰。
  原客户端每页 16 份历史，下一页与上一页无重复，返回第一页身份集合稳定。
- 原客户端真实下载 80,459 字节，选择下载文件后两次显式原文回验。上传正文与下载
  文件完全一致，响应原文 SHA/字节/七项期望/历史标志符合合同。独立标准库重算历史
  封套、原检查点和全部五条收据链通过，保留超过 2^53 的纳秒整数；805 个文件不变。
- 改选损坏文件立即清除旧成功，服务器拒绝并显示 unknown；错归档身份在客户端拒绝，
  不发 HTTP。真实服务器成功响应被传输夹具丢弃时，客户端仍显示失败、无成功回执；
  用户显式重试同一文件成功，没有新增页、任务或持久凭据。
- 挂起另一份真实成功响应并断开连接，释放迟到响应后历史和成功回执不重新出现，
  更新通知计数不变；重连后显式加载，不自动核验或推进。
- 夹具损坏服务器第一份历史，原 stored verify 返回 503。已下载副本仍可通过原文
  回验，证明副本核验没有依赖服务器归档；不修复坏历史、不导入副本。重启后原坏
  历史保留 unknown 并占容量，活动仍 6/32、历史仍 128/128。另一个完整历史重新
  下载并回验 80,460 字节成功；原 partial 扫描仍 512/671，不将文件成功当作当前覆盖。
- 最终冻结 HTTP 实际返回原文 200、跨归属/轮次/错摘要 409、未鉴权 401、重复 JSON
  字段及指数溢出 400、媒体类型 415、声明超限 413。该组核验前后 805 个文件不变；
  无 Content-Length 的实际累计流超限由 ASGI 合同测试覆盖，不混记为冻结网络验收。
- 原完整 CLI 入口（python main.py，同 src.cli:main/vc）在无台账临时 cwd 核验真实下载，
  七项期望成功退出 0，错轮次 unknown 退出 1；cwd 不新增文件，配置日志不写入，
  stderr 为空。Desktop 冻结侧车保持 server-only，不扩展为第二个 CLI 业务入口。
- 当前运行副本启动两次，模型请求 0；70 条旧结果、done/accepted、消费输入保留，
  未新增依赖失效 marker 或未读交接 revision；未请求停止、执行、验收或交接确认。

初次 Python HTTP 测试的 Headers.update 用法错误、前端测试的 Node 类型和 ES2020
不支持的 at 导致测试/构建失败；修正测试后复跑通过，初次日志保留。一次 QA 错用
缺少 verify 后缀的历史路由得到 404，未继续该次副本核验/重启，检查页面后用真实
verify 路由重新验收。曾试图用 server-only 冻结入口运行 coverage-verify：沙箱先拒绝
同步信号量，隔离权限执行后命令解析退出 2；保持原侧车合同，改用完整 CLI 验收。
urllib 全量发送超限正文遇到服务器提前拒绝的 BrokenPipe；改为先发请求头读取实际
413，不把该客户端异常当作原文或预算通过。这些尝试不计入成功验收，记录均保留。

390px 任务区域截图已视觉检查：viewport=390、body=382、card=358、records=314、
文件输入宽 257，无横向溢出。历史当时 complete、所选文件核验及“非外部持久保存/
非当前覆盖/不释放容量”分别显示，文件入口默认折叠。夹具只覆盖 min-width 和滚动
容器，产品 900px 最小宽度保留；不是整套原生 Desktop 的窄屏/文件选择器验收。

浏览器空间已关闭；仅停止本轮精确命令的 Vite、控制器及当前冻结副本，两轮副本
65349/65350、61458/61459 和 Vite 14655 端口均关闭，临时台账及四个 QA 源文件已清理。
Codex 桌面、协调聊天、其它项目进程及既有无关工作区改动未处理，未提交、安装或发布。
证据保存在 /tmp/vorto-fileverify-focused-last.log、vorto-fileverify-contracts-final.log、
vorto-fileverify-full.log、vorto-fileverify-frontend-last.log、vorto-fileverify-desktop-last.log、
vorto-fileverify-sidecar-final.log、vorto-fileverify-export.json、vorto-fileverify-export-restart.json、
vorto-fileverify-independent-verify.json、vorto-fileverify-client-repeat.json、
vorto-fileverify-client-failures.json、vorto-fileverify-client-late.json、
vorto-fileverify-client-copy-independent.json、vorto-fileverify-records-restart.json、
vorto-fileverify-client-capacity-pagination.json、vorto-fileverify-client-final.json、
vorto-fileverify-packaged-contracts.json、vorto-fileverify-offline-cli.json、
vorto-fileverify-final-runtime.json、vorto-fileverify-client-390.png、vorto-fileverify-cleanup.json，
以及同前缀的初次测试、QA route/entry/sandbox/budget-client 失败文件和运行日志。

按用户指令继续暂缓原生应用/窗口/焦点、输入法、Spaces、VoiceOver 与真实 Hook；此前
原生失败/未验收记录未删除或改记通过。真实 provider 硬预算/计费/远端停止、生产环境、
原生 GUI 及正式发布均未验收。摘要一致不是外部签名、外部持久保留、连续稳定或
跨进程事务，不证明停止、验收、交接处理、持久交付或 Goal。下一切片仍需真正的
外部保存/回读凭据与有限退役/迁移记录合同；本轮未提供历史删除/导入/历史容量释放，
未启用自动检查/推进或新 provider 权限。详细合同见 PRD 第 33 节及 TASK_DISPATCH_API.md。

### 显式本地保留与保存凭据核对（2026-10-04，PRD 第 34 节）

承接第 33 节，仅实现迁移的本地保存步骤。原 vc CLI 增加 coverage-preserve 与
coverage-preservation-verify；原 task_scan 复用有界原文/摘要/完整链核验和共享任务锁，
目标专用目录另用非阻塞文件锁。保存经独占临时写入、文件同步、无覆盖发布、目录
同步及回读后生成固定凭据；失败保留原文、旧凭据和原台账，不确认保存或释放历史。
HTTP/GatewayClient/原任务卡只核对原文与凭据的原始字符串，不访问其中的路径，不
增加任务库、执行池、原生存储窗口、后台同步、模型检查或自动推进。

新增 tests/unit/test_task_coverage_preservation.py 共 42 项，覆盖同步/回读/重复/重启、
原台账不变、没有当前来源或保存位置时上传仍只验证一致性、写入/文件同步/无覆盖
发布/目录同步/回读各阶段失败与重试、只有原文的中断组占容量、坏文件/凭据和原文
缺失不修复、相同字节 inode 替换/改名/目录/锁替换、两文件读取期间凭据变化、固定
摘要/轮次布尔类型/覆盖/路径/重复字段/超长、目录符号链接/非目录/不存在/.git/.vortocode/
锁符号链接/FIFO/争用、512 项观察预算、CLI 先于日志初始化、HTTP 鉴权/General/严格
查询与外层字符串/媒体类型/声明及无长度流预算。初次 39 passed/1 failed 是测试夹具
先把 storage 改成字符串后再写其字段，修正测试而未削弱断言；初次日志保留。

最终相关保存/原文/历史/观察/分页/下游/CLI/路由集合 270 passed、7 warnings（8.89 秒）。
完整后端最终 3733 passed、11 skipped、11 warnings（240.23 秒）。前端 24 个文件
291 passed（797 ms），新增 19 项原文字符串、设备/inode 字符串精度、坏凭据/预算、
固定凭据/时间/路径/快照和不信任存储声明的响应合同。TypeScript/Vite 最终构建通过
（22.17 秒，既有大 chunk 警告保留）；Ruff/git diff --check 通过，原生 Rust 未改。
managed CPython 3.13.13 最终冻结侧车为 21,229,392 字节，SHA-256 为
c35141608903de165ff9b56b996756f41f3e7e184d8fae0d35a36d7e2e7ec6e3。

冻结 binary 逐字复制到独立临时目录；沿用 671 条任务、70 条历史 done/accepted/已消费
下游，真实 HTTP 创建五个 complete、一个 512/671 incomplete 和 128 份历史封套。
ego-browser TaskSpace 97 使用原 TaskCard/TaskCoverageRecords/TaskCoverageFileCheck 与
GatewayClient，仅将原生 HTTP 插件替换为浏览器 fetch 代理，无领域响应 mock。验证：

- 从原任务卡真实下载 80,461 字节；完整 CLI 在无台账 cwd 保存到另一明确选择的既有
  临时目录。原文逐字保留，固定保存凭据的摘要由独立标准库重算通过。保存返回
  synced_and_read_back、storage_observed_now=true，但 durable_copy_confirmed=false。
- 新 CLI 进程重复保存返回 replayed=true，相同凭据 ID/摘要/保存时间，三个目标文件
  字节和纳秒 mtime 不变。独立只读 CLI 回验返回 read_back_now；cwd 和配置日志均
  不新增文件，原台账 805 个任务/活动/历史文件字节和纳秒 mtime 不变。
- 对同一个明确保存目录执行真实 CLI，保留 128 组完整原文和凭据（不同空白原文摘要，
  每组完整链均验证，不用伪文件填满）。第 129 组退出 1、unknown，明确给出 128 上限
  与保留旧文件提示，256 个原文/凭据不变，无静默淘汰；该组操作 27.39 秒完成。
- 另一个真实 CLI 进程在目录锁被持有时退出 1/unknown，文件不变；释放锁后同组重试
  replayed=true，满容量仍允许既有组核验。夹具将已保存原文移开后，只读 CLI 返回
  unknown，不把保存凭据单独当作原文还在的证据，不重建或覆盖原文件。
- 浏览器选择实际原文和凭据两次显式核对，外层两个字符串分别与两份文件完全一致。
  返回 receipt_and_uploaded_bytes_consistent、storage_observed_now=false；界面分别显示
  当前上传字节一致、保存记录时间、保存位置“记录值”和服务器没有读取该位置。
  原台账 805 个文件不变，活动/历史仍为 6/32、128/128。
- 更换坏凭据立即清除旧成功；服务器拒绝内部摘要/覆盖变化并显示 unknown。错原文
  摘要凭据在客户端拒绝，HTTP 数量不变。丢弃真实成功响应时界面保持失败，无成功
  保存回执；同文件显式重试成功，不追加扫描页、持久凭据或任务。
- 挂起真实成功响应后断开连接，释放迟到响应未恢复历史或成功凭据，更新通知计数
  不变；重新连接后只显式加载，不自动核对、保存或推进。
- 夹具损坏服务器原历史，stored verify 实际 503；缓存的原文及凭据仍可核对一致。
  重启后坏历史保持 unknown 并占容量，128 份历史保留；原 partial 仍序号 1、读取
  512/671/incomplete。保存位置原文已缺失时上传仍 200、storage_observed_now=false，
  明确不证明存储还在；原台账不因上传回验改变，也不导入副本修复坏历史。
- 冻结接口实际返回正常 200、跨归属/轮次/错凭据原文摘要 409、未鉴权 401、错误媒体
  类型 415、重复外层字段 400、声明超过 17040384 字节 413。无 Content-Length 的
  累计流超限由 ASGI 合同覆盖，未混记为冻结网络验证。
- 当前运行副本启动两次，模型请求 0；70 条旧结果、done/accepted、消费输入保持，
  未新增失效 marker 或未读交接 revision，未请求停止/执行/验收/交接确认。

390px 任务区域截图已视觉核对：viewport=390、body=382、card=358、records=314，
两个文件输入各 257，无横向溢出。长保存位置完整换行，历史当时 complete 与上传
一致分开显示，不把凭据说成当前覆盖或外部持久保存。夹具只调整 min-width 和滚动
容器，产品 900px 最小宽度保留；不是原生 Desktop 文件选择器或整套窄屏验收。

浏览器空间已关闭；只停止本轮精确命令的控制器、当前冻结副本及 Vite，59024/59025/
14656 三个端口关闭，临时台账、专用保存目录、无台账 CLI cwd 和四个自建 QA 源文件
已清理。导出原文及凭据副本、日志与失败记录保留于 /tmp/vorto-preservation-*：
focused-first.log、previous-contracts.log、contracts.log、contracts-final.log、full.log、
frontend.log、desktop-first.log、desktop-final.log、sidecar-final.log、export.json、
upload-receipt.json、cli-qa.json、capacity-qa.json、cli-lock-loss.json、client-repeat.json、
client-failures.json、client-independent.json、records-restart.json、client-restart.json、
client-store-loss.json、final-runtime.json、client-390.png、cleanup.json 和运行日志。
Codex 桌面、协调聊天、其它项目进程及既有无关改动未处理，仓库未提交、安装或发布。

原生应用/窗口/焦点、输入法、Spaces、VoiceOver 和真实 Hook 继续按用户指令暂缓，
此前原生失败及未验收记录保留。真实 provider 硬预算/计费/远端停止、独立存储故障域/
云端/长期保留/电源故障、生产环境、原生 GUI 与正式发布均未验收。本地同步与上传
一致均不是外部签名，不证明停止、验收、交接处理、持久交付或 Goal；原共享锁不提供
跨进程任务事务，目标文件锁不提供分布式租约或恶意替换保证。下一切片仍需有限退役
身份和迁移记录的保存/再迁移及存储准入合同，才可设计显式历史释放；本轮无历史删除/
导入/历史容量释放、自动检查/推进或新 provider 权限。合同见 PRD 第 34 节和接口文档。

## 35. 有限扫描退役身份、独立列表与重启保护（2026-10-04）

源码已实现：先复现释放后移走归档、同旧请求重新创建扫描的缺口，再在原 TaskLedger /
task_scan / TASK_STATE_LOCK 内补齐有限退役身份。显式释放先核验原归档，再同步/回读
不可覆盖的退役凭据，最后移除活动文件；不创建任务数据库、依赖索引或执行池。
128 份/8192 字节保持有限，损坏资料占容量；已保存但释放失败的活动版本保持不变，
扫描/status/observe 拒绝推进，按原合同重试。旧版本只在明确操作时补记，不自动迁移。
独立列表按固定 scan_id 升序、16 项一页；只读 HTTP 与离线 CLI 核验/导出。

最终 Python 全集 3764 passed / 11 skipped / 11 warnings，209.93s；新增退役合同 31 项，
覆盖归档移走、不可变重试/重启、旧版显式补记、保存/发布/同步/回读失败、移除中断、
原检查点冻结、容量满/坏记录计数、独立分页、损坏/超长/重复字段/链接/目录、摘要结构、
跨归属/轮次、原任务删除后的只读核验与 CLI/HTTP。阶段相关回归 299 passed / 7 warnings，
10.46s，最终全集包括随后补充的目录链接拒绝。前端 309 passed / 24 files，783ms，
验证身份/快照/原归档绑定、未知状态拒绝、历史属性及有限分页/容量。Ruff 与 diff 检查通过。
原有 skip、弃用告警、原生失败断言与未验收记录保留。

本地 TypeScript/Vite 构建通过，保留既有大 chunk 告警。CPython 3.13.13 候选冻结侧车构建
通过，浏览器验收使用候选运行包 21237824 字节，SHA-256：
`e7dabb682012ea5f404bea57aa416bbe61647274c99aaef4fdf4b860a50800d6`。
独立临时目录中的逐字节副本核验一致；侧车仍 server-only，不安装或启动原生 Desktop。
源码 CLI 在无台账 cwd、配置日志路径的情况下真实执行导出退役 JSON 回验，身份/摘要
绑定成功，错误轮次 exit 1 / unknown，cwd 和日志路径未写入。浏览器下载的 compact
退役原文 1092 字节，独立计算 evidence SHA 与凭据一致；此记录不包含 lstat 大整数。

浏览器候选冻结副本使用临时 671 项台账、70 项原 done/accepted/consumed 的依赖历史和本地模型
调用陷阱。Ego TaskSpace 101 在原 TaskCard、TaskCoverageRecords 和 GatewayClient 上
验证：实际释放活动 5→4、保留归档 3，旧版补记保持活动 4；服务端成功后网络响应丢失
只显示错误，重新加载确认同凭据，API 重放保持原摘要/时间/文件 mtime。归档在隔离
测试中显式移至保留目录，旧请求 HTTP 409，独立退役列表和 GET 仍可只读核验。
重启保持全部凭据；通过实际分页/归档/释放合同增加 18 项，20 项退役身份在客户端
按 16+4 呈现，无重复，无合并为当前覆盖。客户端只读核验响应被暂存后断开连接，
释放迟到响应没有新增 onUpdated、成功信息或写入新连接；重新连接需显式加载。

模拟一份损坏凭据，再加入 108 份坏资料使实际容量达到 128，最终 19 项可核验、109 项
unknown 均占容量。新身份释放 HTTP 409，活动与全部历史/退役文件摘要和 mtime 不变；
已有身份重放仍成功。再次重启保持容量、unknown 和拒绝旧损坏身份重建的 HTTP 503。
客户端实际点击新释放请求显示可理解的 128 上限提示，不吞掉活动文件；最终客户端
加严原归档/快照绑定后再次真实只读核验成功。671 个原任务文件逐字节/mtime 保持，
原 512/671 的 incomplete 检查点未改写，70 项结果/消费版本/验收保留，失效 marker
消息和模型调用均为 0。自动检查、自动推进及 provider 权限保持原边界。

收尾的有限目录预算检查又发现：256 项残留文件可能使新凭据发布后的目录超限。
最终补丁在发布前预留最终目录项：256 项拒绝，255 项允许保存至最终 256 项；既有
身份重放不新增项，所有残留资料保留。最终全集增加该合同；另外一次 TypeScript
构建发现状态联合类型未显式收窄（TS2345，taskCoverageArchive.ts），补齐 retired
判定后重跑 309 项和 TypeScript/Vite 通过（21.53s），失败摘录保留，不改记初次通过。
最终冻结包 21238048 字节，SHA-256：
`b9f0bd751ceb4cc439ce34ab365bf7a2b1b6248d0251fbe9a24113e23b4c5755`。
在另一临时目录运行字节相同的最终副本，用原 GatewayClient + global fetch 适配做无
界面真实客户端验证：256 个残留项时收到可读拒绝，原 JSON/活动文件及残留名称保持；
隔离测试显式把一项残留移动到保留位置后，实际释放、固定身份/原归档/快照绑定、
原文导出、不可变重放、原归档移走后拒绝旧请求和重启均通过。最终 671 份任务与原
512/671 incomplete 检查点仍不变，70 项历史保留，模型调用为 0。此无界面收尾验证
与前述候选包上的真实浏览器 UI 证据分别记录；最终目录预算补丁后未重新打开已关闭
的浏览器空间，不声称做了新版完整浏览器或原生 GUI 重验。

浏览器 390px 检查无横向溢出，历史/当前覆盖边界及按钮可读。初始临时夹具遗漏 #root
滚动使下方按钮不可见；只修正自建 QA 夹具，并在同一个空间恢复，初始失败截图保留。
热更新重置资料列表后显式重新加载，未另开浏览器空间。产品原 900px 最小宽度保留；
该截图不是原生文件选择器或整套窄屏验收。空间已 finish(keep=[]) 一次关闭；只停止
本轮精确命令的控制器、冻结副本及 .retirement-qa Vite，临时台账和四个自建 QA 源文件
清理，60296/60298/14657 端口关闭。Codex、协调聊天及其他项目进程未处理，未提交、
安装或正式发布。完整日志、导出与失败证据保留于 `/tmp/vorto-retirement-*`：
full-tests.log、contract-tests.log、frontend-tests.log、frontend-build-final.log、sidecar-build.log、
runtime-identity.json、export.json、independent-qa.json、client-lost-response.json、
client-pagination.json、client-late.json、client-390.png、client-initial.png、client-capacity.json、
client-capacity.png、client-final.json、capacity-restart-qa.json、baseline-fingerprints.json、
final-fingerprints.json、cleanup.json 和控制器/运行副本/Vite 日志。
最终补丁的 full-tests-final.log、sidecar-build-final.log、frontend-build-type-failure.log、
final-runtime-client.json、final-cleanup.json、final-qa-info.json 及 final-controller/
final-packaged-runtime 日志另行保留；第二副本的 52705/52706 端口和临时台账已清理。

原生应用/窗口/焦点、输入法、Spaces、VoiceOver 和真实 Hook 继续暂缓；此前失败和
未验收记录不删除或改记通过。真实 provider 硬预算/计费/远端停止、独立存储故障域、
长期迁移/云端/电源故障、原生 GUI、生产环境和正式发布均未验收。退役 JSON 只验证
自身摘要/身份结构，原完整收据链仍需原归档，不是外部签名或迁移/持久保留凭据。
退役不证明任务停止、验收、交接处理、持久交付或 Goal；活动残留仍可能占容量。
不提供历史/退役删除、导入、历史容量释放、自动检查/推进或新 provider 权限。下一阶段
继续设计迁移记录、退役资料自身保存/再迁移、目标故障域准入与显式历史释放合同。
原共享锁仍只有单进程保证。合同见 PRD 第 35 节和 TASK_DISPATCH_API.md。
