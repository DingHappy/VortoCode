# Desktop 首次交付验收

## 2026-10-24 安装与真实模型验收

- 本机已安装并启动 0.2.15，构建 `20261024T081857193Z-e1cbc94d`。Developer ID、Apple 公证、装订票据、Gatekeeper 与内置引擎健康检查通过；五个本机会话的身份与消息数量在安装前后相同，旧应用和本机数据已验证备份。
- 已安装原生应用恢复了常驻电脑连接及 18 条实际动态。真实简报可展开，切换待处理再返回仍保持展开。没有为验证界面而批准生产任务或发送 IM 消息。
- 另建一次性 Git 仓库：`pricing.py` 包含折扣符号错误，标准库 unittest 的两项测试中一项失败。通过已安装 Desktop 发出只回复指定文字、不调用工具的消息，会员 `mimo-v2.6-pro` 在约 3.1 秒后返回正确文字；这证明一次真实主聊天请求成功。
- 随后的受限隔离修复任务正确读取两份文件并调用 `dev_isolated`，但实现角色的本地 `Qwen3.6-35B-A3B-4bit` 返回 HTTP 507：模型约需 19.95GB，超过约 13.29GB 动态内存上限。内置重试两次后停止并报告阻塞。独立核对 main HEAD 与初始提交相同、跟踪文件没有差异、没有交付分支；因此不能把该任务计为完成，确认、产物审查和最终验收链路也没有全部通过。
- 用户明确选择临时使用已配置会员云端模型后，仅将实现角色改为 `vortocode_member:mimo-v2.6-pro` 并重启当前临时项目引擎。同一任务在约 58 秒后成功交付 `vorto/pricing-py-discounted-total--bf1f3a82`，提交 `adf699050ed0b7e69146debfaff8c52ff7d3fbd6`。独立检查 main 仍为初始提交 `804165186c271b09c1e54c638fb91cfa2407a4d1`，跟踪文件无差异，交付分支只改 `pricing.py` 一行（+1/-1），未改测试或配置。
- 原生 Desktop 的「变更」面板可直接展开该交付，显示固定基线、提交、单文件 diff、实际验证命令和输出；重启后交付记录仍可读取。流水线实际保存的命令是 `python3 -m pytest -q test_pricing.py`（解释器为已配置的 Miniforge Python），两项通过。主聊天却声称运行了请求中的 unittest，存在报告不一致；另从交付提交导出到独立临时目录，用系统 Python 运行 `python3 -m unittest -q`，独立确认两项通过。该补跑不能证明流水线执行了原指定命令，后续应约束最终回复引用实际验证记录。
- 验收后已清除实现角色的临时覆盖并保存，恢复原先留空、跟随本机默认模型的配置，当前引擎重新连接。没有新增供应商、凭据或调整模型内存上限，也没有 push、PR 或向他人发送消息。模型费用未知。本次证明一次云端隔离修复及 GUI 交付审查可用；本地模型容量问题仍在，单独弹出执行确认卡的路径仍未完成验收。
- 后续源码补齐动态来源失败、过期及不完整数量提示，并使用合成数据验证；这些新增提示尚未编入上述已安装构建。验收截图与本机证据保存在被 Git 忽略的 `.vortocode/acceptance/20261024-p0/`，安装证据在 `.vortocode/install-evidence/20261024-latest/`。
- 来源可信度合成界面验收覆盖失败保留旧记录、过期、断线、成功恢复和未知数量；没有执行真实批准动作。最终本地门禁：Python 4994 项通过、10 项跳过，E2E 25 项通过，前端 957 项通过，Rust 108 项通过、1 项忽略；类型检查、应用及 QA 构建通过。它们与上述真实模型、原生界面证据分别记录。

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
sidecar 已重新构建，冻结模块清单包含 src.workflow.tasks.check_reports；临时工作目录中的
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

原开发恢复入口收敛到 workflow/tasks/task_recovery.py，复用 TaskLedger、TaskRunner 和隔离
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

## 36. Desktop 跨会话看板首版（2026-10-07）

源码：原 RuntimeInboxPanel 新增默认四列看板、保留按项目视图及展开/收起；TaskBoard 与
纯投影模块独立实现项目/会话/Goal 交集筛选、来源导航、原 Goal 设置入口。CLI/Gateway
源码、原 TaskLedger/执行池与自动检查/推进/provider 权限未改变。合同见
DESKTOP_TASK_BOARD.md、TASK_COLLABORATION_PRD.md 第 36 节。

自动验证：前端全部 348 项 / 32 文件通过，其中看板新增 11 项合同与静态渲染测试。
既有 Inbox 与 Goal 合同 14 项通过 / 1 条既有 Starlette 弃用提示；本轮未重跑整个后端
回归。TypeScript（包含 QA fixture）和正式 Desktop Vite 构建通过，最后构建 19.85 秒，
原有 >500 kB chunk 提示保留。git diff --check 通过。运行日志保存在
`/tmp/vorto-board-tests.log`、`/tmp/vorto-board-backend-contracts.log`、
`/tmp/vorto-board-build.log`。

独立运行验证：单独构建原 Desktop RuntimeInboxPanel/TaskBoard 为静态生产资源
`/tmp/vorto-board-isolated`，仅绑定本机临时端口 51067；使用两个项目的固定摘要，故意
复用 task/session/Goal ID，以导航回调记录来源。此 fixture 不接真实 Gateway、凭据、
模型或持久化；正式 dist 不包含 QA 页面。可复用入口为 desktop/qa/task-board.html，
独立配置 qa/vite.config.ts；这是隔离组件包，不是新 .app 或冻结 sidecar 的验收。

Ego-browser TaskSpace 105 的真实浏览器操作已通过：

- 项目 B / 会话 one / Goal goal-shared 的交集只留下对应任务；任务导航精确返回
  `[b, tasks, one, same-id]`，不会定位同名项目 A 任务。
- 设置 Goal 返回 `[b, goals]`；任务类待处理事项同样带 owner 与目标 task ID。
- B 失联后保留历史卡片且所有卡片和 Goal 入口禁用；移除 B 后原筛选保持“来源已
  移除”、零卡片，不默认为另一个项目。切换到 A 与旧按项目视图仍可工作。
- done 任务停留待核对，已完成列只显示权威 achieved Goal；展开/收起工作。
- 1400px 页面宽度 1400、四列存在；390px 的 fixture-only 外层宽度覆盖后页面宽度
  390，列容器 324 / 内容 996，横向滚动留在列内。正式 Desktop 的 900px 最小宽度
  保留。初次窄视口得到页面宽度 900 的记录保留，没有将该断言改记通过。

浏览器证据 `/tmp/vorto-board-browser-acceptance.json` 保留首次交互及原窄视口限制，
`/tmp/vorto-board-browser-final-layout.json` 保存隔离外层修正后的尺寸断言。两次截图
均在 CDP captureScreenshot 超时，截图/视觉验收仍未通过；DOM 与交互通过不能替代
视觉验收。未尝试原生窗口控制、解锁、焦点或 Hook。TaskSpace 105 已 finish({keep:[]})
一次关闭；本轮仅启动的静态服务在验收后停止，其他项目、Codex 与协调会话未处理。

真实 Gateway 跨项目连接/目标保存/任务操作全链路、新原生 .app/sidecar 运行包、真实
provider 与原生 GUI 均未验收；原生验证按用户要求继续暂缓。未正式发布或安装。
本轮只证明看板源码、构建、隔离包和浏览器组件交互。此前失败与未验收记录保留。

下一步：先扩展原摘要的权威验收/交接/有效性字段，减少历史已处理任务的待核对卡片；
再做同项目跨会话 Goal 关联、精确 Goal 定位和完整台账分页。本轮未实现这些写合同，
也没有开启拖拽执行、自动推进、自动模型检查或新增 provider 权限。

## 37. 看板权威任务摘要与真实会话来源（2026-10-07）

源码：原 runtime Inbox 增加可选 lifecycle version=1，TaskLedger 的原共享锁包住读取；
task_summary.py 只用当前 200 条前缀和总共 2048 次内存来源查询，复用原验收形状、
结果版本回执、依赖核对和恢复血缘。缺来源/损坏/耗尽 unknown，观测冲突或已固定失效
invalid；不读额外任务文件、不停止/执行/验收、不改任务或 Goal。CLI 无新看板；常驻
端仅增加只读数据字段。Desktop 校验完成组合并区分验收/交接/依赖有效性，原项目视图
同样不漏 done 待处理。原 owner 的单个 sid- 前缀只在 Desktop 导航边界转换。

本地验证：前端全部 359 项 / 32 文件通过；相关后端 226 项通过 / 1 条既有 Starlette
弃用提示；另重跑 Inbox 5 项，包含新增“完成任务不改变已关联 Goal/验收证据”边界。
这两次后端集合共 227 个不同测试，不是一次全量后端回归。覆盖已处理但未验收、已
验收但待交接、同文新轮次、改结果/损坏回执、未知/损坏来源、预算耗尽、归属/轮次变化、
已固定失效/前置恢复、重启、旧恢复历史、不改源数据和新旧客户端兼容。TypeScript 与
正式 Desktop Vite 构建通过（22.20 秒，既有 chunk 提示保留）；新增最后的历史呈现测试
后再次 TypeScript 检查通过，产品源码未再变化。Ruff/git diff --check 通过。
日志为 `/tmp/vorto-board-lifecycle-{frontend,backend,build,final-types,goal-boundary}.log`。

独立运行验证：`desktop/qa/task-board-server.py` 在专用临时根
`/tmp/vorto-board-lifecycle-icatz56j` 复制 src，使用独立 TaskLedger/会话/Goal；未接项目
原台账。手动 seed 记录，挂原 `/api/runtime-inbox`、`/api/task-inbox/.../acknowledge`
路由；不启动完整 agent runtime、worker 或真实 provider，模型构造被禁止。
独立 Vite 生产资源 `/tmp/vorto-board-lifecycle-assets` 使用原 GatewayClient 和原
RuntimeInboxPanel/TaskBoard；仅 QA 把 Tauri HTTP 插件传输替换为浏览器 fetch，正式
配置不变。此为独立源码/组件包，不是新 .app、冻结 sidecar 二进制或原生传输验收。

Ego-browser TaskSpace 106 通过原客户端和真实本机 HTTP 验证：

- 实际服务返回 owner_session=sid-owner，持久会话 sid=owner。会话筛选保留对应任务，
  来源导航精确为 `[a,tasks,owner,task-parent]`；旧按项目视图也返回正确 owner/task。
- 初始 parent 已验收但未处理交接，child 已验收/handled 且依赖 valid，dev 交接已
  处理但“不代表验收”，缺来源 unknown 仍待处理。所有只读筛选/导航前后的任务文件
  SHA 完全一致。
- 浏览器通过原交接 API 处理 parent 当前结果（200）后进入完成列；测试突变端点
  修改临时 parent 结果后，child 保留 done/result/accepted，显示 invalid 且待处理。
- 显式测试核对使用原 invalidate 领域路径固定 child 失效。保留变更前后证据，再将
  临时 parent 原文恢复并重启同一台账；child 仍 invalid，parent 当前交接保持 handled，
  缺来源仍 unknown。旧 child revision 的回执请求返回 409；Goal 未被任务投影升级。
- 最终独立 runtime 的四个变更模块 SHA 与工作区一致；模型构造记录始终为 0。

初次浏览器点击因 fixture 根容器继承 overflow:hidden 导致按钮不可见的失败保留；
只调整 QA 外层 overflow 后重跑通过，未改变产品窗口最小宽度。最终截图再次在
Page.captureScreenshot 超时，视觉/截图仍未验收，没有把第 36 节失败改为通过。最终
DOM 记录为 700px 页面宽度 700、四列容器存在；它不能替代视觉或原生包验收。

证据 `/tmp/vorto-board-lifecycle-initial.json`、`/tmp/vorto-board-lifecycle-browser.json`、
`/tmp/vorto-board-lifecycle-restart.json`、`/tmp/vorto-board-lifecycle-browser-final.json`。
临时根保留 candidate 源码、host 元数据、改变后原始 parent 记录、最终模块 SHA 与
cleanup.json。TaskSpace 106 已 finish({keep:[]}) 一次关闭，本轮唯一端口 51773 的服务
经 PID/命令验证后停止；没有处理 Codex、协调会话或其他项目进程。

原生验证继续按用户要求暂缓；已安装 .app/原生插件传输、完整常驻端启动、真实
provider/硬预算、正式发布均未验收，没有安装或发布。独立临时 API 证据不能替代
真实用户项目与生产权限环境。自动检查/自动推进和历史迁移边界保持原状。
下一步同项目跨会话 Goal 关联合同与精确定位；全量看板分页另行设计。

## 38. Desktop 精确 Goal 定位（2026-10-07）

承接第37节，完成精确定位切片；跨会话关联写入并未实现，合同边界见 PRD 第38节。
保留第36/37节截图失败和全部原生未完成记录。本轮不操作原生 GUI。

### 源码与测试

- 原 GatewayClient 单项 getGoal，原 useGoals 的定位请求代次/client 固定，列表读取
  防迟到覆盖；原 GoalsPanel 显示精确 ID、单项目标或明确未核验状态。
- 看板 Goal、已有任务 Goal 引用、Goal 事项与按项目列表传入来源 runtime/精确 ID。
  原 App 复用项目激活；查看项目 Goal 不切换会话，不提交 Goal run 或验收。
- 加入旧请求失败/成功、同项目重置、相同 ID 跨客户端、404、错误身份、重试、
  无连接、摘要外单项、隐藏旧卡操作、原列表更新、旧删除不清理新定位测试。
- 全前端最终 369 项/34 文件通过；TypeScript 随生产 build 通过。相关后端
  test_goals/test_goal_revision/test_gateway_runtime_inbox 共33项通过（8.64秒），
  1个既有 Starlette 弃用警告。没有声称全后端或 Rust/sidecar 回归通过。
- 日志：`/tmp/vorto-goal-navigation-tests.log`、`/tmp/vorto-goal-navigation-build.log`、
  `/tmp/vorto-goal-navigation-backend.log`、`/tmp/vorto-goal-navigation-qa-build.log`。
  初次 TypeScript 因测试使用 Array.at 超出目标库失败，改索引后通过；一次 SSR
  断言把通用提示“指定目标”误作旧标题，改为精确 h3 断言后通过，失败记录不作通过依据。

### 独立运行与实际浏览器

- 独立生产 QA 资源 `/tmp/vorto-goal-navigation-assets`；原源码拷贝运行目录
  `/tmp/vorto-board-lifecycle-8qc11sa7/runtime`，独立临时工作区在同根 workspace。
  仅装配原 Inbox/交接/Goal routers；模型构造陷阱记录为空，非真实 provider。
- 本轮 Ego TaskSpace107 的实际 DOM 操作：看板查看指定 Goal，原 getGoal 读取
  `goal-458aa7895f`、呈现原合同与0/1验收；缺失 goal-missing 返回404，旧目标卡数量0；
  原 ID 显式重试保持未核验；明确返回列表恢复，再从原项目列表定位同一 ID。
- 相同 Goal ID 的两个静态项目 fixture：任务“查看关联 Goal”回执分别为
  `[a,goals,"","",goal-shared]`、`[b,goals,"","",goal-shared]`。
  这验证原组件来源路由，不能证明完整 App 连接/项目启动过程或真实任务关联写入。
- 看板定位前后、缺失/重试/返回列表和原列表定位后，5个任务及1个 Goal 文件 SHA
  完全相同。模型调用数组长度0。浏览器核验脚本初次将数组与数字0比较而失败，
  修正为数组长度后验证通过；保留该失败原因于 final JSON，不改写为产品失败/通过。
- 证据：`/tmp/vorto-goal-navigation-browser.json`、`/tmp/vorto-goal-navigation-missing.json`、
  `/tmp/vorto-goal-navigation-final.json`、`/tmp/vorto-goal-navigation-static.json`。
- TaskSpace107 已 finish keep[]，仅本轮 PID82428/端口59941经命令行和监听端口双重
  校验后停止，清理凭据保存同根 cleanup.json；未处理其它会话进程。
- 最后的删除迟到响应隔离及 App 返回列表刷新补丁以新增合同测试与最终构建验收；
  已结束的浏览器轮次没有再次测试删除写入。实际浏览器验证覆盖定位/读取/错误恢复，
  不声称验证全部原 Goal 写操作。最终 QA 包随后重新构建，未作为已浏览器逐项验证的包。

### 验收层级

源码/合同测试与生产前端构建通过；独立拷贝原接口+客户端组件的浏览器读取路径通过。
独立 QA 前端资源不是原生 .app、完整 Gateway 启动或已安装 Desktop。
截图视觉未验收（本轮未再尝试截图），原生 GUI/真实 provider 未验收且继续暂缓，
没有新增自动检查/自动推进/provider授权；没有正式发布、安装、提交或部署。


## 39. 本机模型供应商与角色路由（2026-10-07）

承接用户“本地的agent的配置接入……本地的模型”方向，完成本机空 Key 供应商路由
和网络/凭据隔离切片。Goal 关联写入仍未完成；不改变第38节已完成或未验收记录。

### 实现与合同

- 修复 Desktop 允许回环空 Key、后端却拒绝端点的断点。原 providers 登记显式回环
  端点时补固定非秘密 `not-required`，原 client_for_spec/工序角色/chat路由均复用。
- 显式传入本机 LLMConfig 空 Key 不再回填默认云端 Key；本机自己的 Key 保留。
  回环 URL 严格解析 hostname、scheme、凭据、query、fragment、port；远程无 Key
  和伪 localhost 主机无本机豁免。SDK 与 aiohttp 本机 transport 不读代理；公网不变。
- Desktop 自定义供应商包含本机预设；移除原 startsWith 的主机豁免，主模型设置共用
  URL判断。本机鉴权和配置文字明确；保留既有保存确认与当前 runtime 重启机制。
- 不写实际 profile/.env，不启动/下载真实模型，不改 provider 权限、预算、执行池或
  自动推进。配置、角色文件示例和官方协议来源见 LOCAL_MODELS.md。

### 源码、测试及构建

- 最终相关后端114项通过（2.39秒），覆盖 providers、routing、全部 test_llm*、
  task_collaboration 和 dispatch_service；不是完整后端回归。
- 第一次扩大回归110通过、4个既有 list_models 测试因沙箱禁止绑定端口失败。
  原记录保留 `/tmp/vorto-local-model-regression-sandbox-blocked.log`；获自动允许本机
  专属测试端口后完整114通过，最终 `/tmp/vorto-local-model-regression.log`。
- 前端370项/34文件通过，TypeScript和生产前端Vite build通过（19.22秒），保留既有
  chunk >500k提示。日志 `/tmp/vorto-local-model-frontend.log`、
  `/tmp/vorto-local-model-build.log`。Ruff与diff检查通过；没有修改Rust或重建.app/sidecar。

### 隔离 HTTP 与独立源码副本

- 新 integration HTTP fixture 随机本机端口，原模型清单读取、原客户端聊天和401失败，
  均使用合成响应；设置坏 HTTP/HTTPS_PROXY、清空NO_PROXY，确认本机仍直连。
  真实请求接收头只见本机占位符，不包含云端canary；401只有一次请求，不借Key/重试。
- 工作区2 passed、1 skipped（1.71秒）。独立复制源码目录
  `/tmp/vorto-local-model-validation-35c5ygf7` 同样2 passed、1 skipped（1.70秒），
  明确打印导入路径为该副本 src/llm/client.py。两个原模块 SHA记录 metadata.json，
  另存 `/tmp/vorto-local-model-isolated-root.json`；测试服务在fixture finally 自停。
- `/tmp/vorto-local-model-http.log`、`/tmp/vorto-local-model-isolated.log` 为最终日志。
  此副本是源码隔离运行验收，不是已打包/安装的Desktop运行包。
- 首次试验原生工具 HTTP 回路失败：环境无 OpenAI SDK，原 aiohttp降级不返回
  tool_calls。现在普通聊天与原生工具回路分别验证，后者按可选依赖缺失明确skip，
  未改为通过。单元测试SDK注入替身验证 trust_env=False，但不证明真实SDK工具调用。

### 真实服务及其它未验收层

- 只读检查本机11434/11435/1234的 `/v1/models`，初次默认代理检查均Timeout；
  显式禁用代理后均URLError，未核验到可用服务（不据此断言所有本机服务不存在）。
  结果 `/tmp/vorto-local-model-existing-services.json`。未启动Ollama/LM Studio，未
  下载或加载模型，未停止任何其它项目、Codex桌面端或协调进程。
- 实际本机模型推理/流式/工具能力、真实SDK工具HTTP、真实provider硬预算未验收。
  本轮未操作浏览器设置保存或原生GUI，已有原生暂缓/失败记录保持；正式发布、安装
  与部署未做。真实配置保存/重启仍待原生验收，不将前端build视为原生保存已通过。
- 下一阶段：明确用户选择的已运行服务与实际模型ID，在具备SDK依赖后验证原工具
  Agent回路/流式/失败恢复。不能因本机或普通聊天通过而启用自动模型检查或推进。

## 40. 本机角色工具回路与真实SDK隔离验收（2026-10-07）

承接第39节 SDK 缺失的未验收项。本轮临时真实SDK证据独立记录，不删除第39节skip/失败。

### 源码修复

- `LLMClient.chat/stream_chat` 带原生tools但无SDK时明确报依赖缺失，不把工具合同
  静默降级为普通请求。两条入口均验证未调用aiohttp普通聊天发送路径。
- 原非流式SDK响应省略usage时仍返回正文/工具结果，usage为{}；原记账估算保留，
  不能把{}当provider精确用量或硬预算证明。
- `build_subagent` 沿用 `native_default()`；自定义角色遵循原VORTOCODE_NATIVE_TOOLS
  开关，0明确关闭。只读角色工具面仍无run_command/task，不新增权限或执行池。

### 临时SDK、测试与构建层

- 先尝试已有缓存离线安装，但jiter依赖不齐而失败；随后固定OpenAI SDK3.14.1及其
  依赖安装在 `/tmp/vorto-local-sdk-deps`，缓存 `/tmp/vorto-local-sdk-cache`。
  用户Python环境、项目实际.env/profile及真实模型服务均未改。不是已安装runtime升级。
- 临时真实SDK相关回归174项通过（2.79秒），最终日志
  `/tmp/vorto-local-sdk-regression.log`；模型/provider/路由/全部test_llm*、原任务协作/
  下派/角色装配/问题与交付工具合同。不是完整后端回归。
- 用户原无SDK解释器相关61项通过（0.46秒），日志
  `/tmp/vorto-local-sdk-no-sdk.log`。SDK spy单元测试不替代真实SDK HTTP合同。
- Python变更模块编译、Ruff、diff检查通过。前端未改，本轮未重跑前端构建；保留
  第39节370项前端与生产前端build记录。不声明新.app/sidecar/Rust或正式运行包通过。

### 原客户端/角色与独立运行验收

- 实际本机HTTP/SSE socket、原LLMClient与真实SDK，共7项通过（4.48秒），无skip：
  模型清单/普通聊天、401单次失败、非流式工具schema、usage缺失、正文/推理与工具
  JSON参数分片、原角色只读工具回路、流式中断关闭响应及显式新请求复用连接。
  设置坏代理、清空NO_PROXY后仍直连，收到的auth为非秘密占位符，不含云端canary。
- 原角色文件 model=local:fixture:7b 经原registry/build_subagent/MainAgent执行原
  read_file，读到真实临时README标记；下一次请求包含role=tool与该标记，返回最终
  结论，README未改。两次请求同本机端点/实际模型ID fixture:7b。
  未创建正式任务或证明done/验收/交接/持久交付/Goal达成。
- 第一次角色回路仍返回普通fixture回答：复核装配遗漏native设置，同时测试套件
  conftest默认native=0；修复原装配并在该用例显式设为1，后续通过。没有放宽角色工具面。
- 工作区最终日志 `/tmp/vorto-local-sdk-http.log`。独立复制源码
  `/tmp/vorto-local-sdk-validation-jad7jfd2` 使用同临时SDK，七项再次通过；导入路径打印
  为该副本src/llm/client.py，SDK版本3.14.1。最终
  `/tmp/vorto-local-sdk-isolated.log`、`/tmp/vorto-local-sdk-isolated-root.json`。
  metadata.json保留源码SHA与14个临时依赖版本，源码SHA与最终工作区一致。
- 末次对齐副本时一次脚本误以临时cwd作源路径触发SameFileError；随后的默认沙箱
  重跑被本机端口权限阻止，保留 `/tmp/vorto-local-sdk-isolated-sandbox-blocked.log`。
  修正为绝对项目源路径、沿用获准专属端口后最终复验通过。失败不作为通过依据。
- 合成服务由fixture finally shutdown/server_close/thread.join自停；未停止任何其它
  模型服务、项目会话、Codex桌面端或协调进程。临时依赖与证据保留以便复核。

### 保留的边界

本轮通过的是实际SDK+合成模型HTTP/SSE+原角色/工具程序的隔离合同，不是实际Ollama/
LM Studio模型推理或质量验收。未再次启动/探测真实服务，延续第39节实际服务未核验状态。
真实模型工具可靠性、流式质量、provider硬预算、实际runtime依赖安装/启动及Desktop
原生配置保存重启未验收；本轮不做浏览器或原生GUI，不改此前原生暂缓/失败记录。
未发布、安装正式包、提交或部署，也未新增自动模型检查/自动推进/provider授权。


## 41. 用户实际oMLX服务与配置入口（2026-10-08）

用户纠正实际使用oMLX。只读复核源码、工作区及真实进程/监听端口，保护两个服务和其它
项目/会话；没有原生窗口控制、启动、停止或推理调用。

### 实际服务发现

- 只读发现omlx-server PID30789监听127.0.0.1:11500、PID88424监听127.0.0.1:8100。
  无认证GET /v1/models：11500返回401，8100返回200、owned_by=omlx，模型ID为
  Qwen3.6-35B-A3B-4bit。端口非软件默认值，之前仅11434/11435/1234检查不能覆盖它们。
- 原VortoCode客户端使用进程局部OMLX供应商配置读取8100模型清单成功；无真实.env/
  profile/角色目录写入，未读/打印服务Key。11500未继续鉴权探测，不借云端Key或绕过401。
- 同第40节独立源码副本 `/tmp/vorto-local-sdk-validation-jad7jfd2` 的原providers/client
  再次读取8100清单成功，导入路径为副本src/llm/providers.py。三个运行模块SHA与最终
  工作区相同，复用临时SDK，非新运行包或已安装Desktop。
- 证据 `/tmp/vorto-omlx-discovery.json`、`/tmp/vorto-omlx-client-discovery.json`、
  `/tmp/vorto-omlx-isolated-discovery.json`。操作均为list_models，inference_sent=false。

### 源码、合同与构建

- 原Desktop设置/自定义供应商的共享预设增加oMLX，官方默认http://127.0.0.1:8000/v1
  可编辑，不把用户8100/11500或当前模型硬编码为全局默认。model仍空，取实际服务清单。
  精确预设匹配优先于泛用回环分类，未知端口不推断服务品牌。
- 后端复用现有omlx:<模型ID>通用供应商路由，新增测试保护完整模型ID及实例自己的
  token，沿用本机空Key占位符/代理隔离，不新增provider授权或角色工具面。
- 全前端371项/34文件通过；相关后端49项通过（0.39秒）；TypeScript与生产前端build
  通过（19.25秒），保留既有大chunk提示。Ruff与diff检查通过。
  `/tmp/vorto-omlx-frontend.log`、`/tmp/vorto-omlx-backend.log`、`/tmp/vorto-omlx-build.log`。
  最后仅测试名称/文档措辞调整，没有在构建后改产品代码。
- 本轮未改Python运行源码；后端接口/SDK使用第40节已有能力，没有创建另一套适配器。
  当前机器的oMLX配置步骤与官方来源见LOCAL_MODELS.md，.env.example提供可编辑示例。

### 验收边界

本轮真实服务只读模型清单已验收，不能据此证明推理鉴权、工具调用、流式、上下文、质量
或硬预算。11500的鉴权仍未满足；8100也没有发送chat或加载模型。不停止现有实例、不
触碰其它会话进程。原生GUI/配置保存重启继续暂缓，未做浏览器配置保存；CLI/runtime
真实配置未改，正式.app/sidecar运行包、安装与发布未验收/未执行。
第39节常用端口超时/不可用记录、第40节合成模型与SDK合同保留，不改记为oMLX推理通过。
自动模型检查/推进/provider权限保持原边界，下一阶段按选定oMLX实例进行明确有界推理验证。


## 42. 模型连接检查身份隔离（2026-10-08）

- 原 LlmConnectionCheck 提取有界请求控制到 lib/llmConnectionCheck.ts，绑定草稿与代次。
  layout effect 在字段变化/卸载时作废请求；状态只展示当前草稿；地址/Key变化清空候选。
- 新增7项合同测试：当前成功、地址/Key/模型变化后迟到成功、reset后迟到错误、旧结束
  不影响新请求忙碌状态、当前错误可恢复。未新增推理、配置保存、服务启动/停止调用。
- 首次 TypeScript 检查因测试数组.at不在原目标lib中失败；改用索引，不扩大编译目标。
- 测试/构建最终证据见 /tmp/vorto-connection-check-tests.log 与
  /tmp/vorto-connection-check-build.log；最终数量及通过情况以日志为准。
- 本轮只做源码、合同测试和生产前端构建。浏览器实际交互、独立.app/sidecar运行包、
  原生GUI、真实provider推理、配置保存重启未验收；正式发布未执行，旧失败记录保留。
- 实际配置仍为云端；按用户指令8100实例已正常终止，11500后台可达（HTTP200），
  不意味着11500模型推理认证已满足。第41节8100读取证据仅作为历史保留。


## 43. 共享 Agent 协议提示收敛（2026-10-08）

- 源码：原agent_loop.py分离原生schema与提示式JSON说明，同回合永久回退立即切换；
  build能力文案限定原工具/权限范围，两种协议纳入原恢复行为指纹。未修改Desktop前端。
- 复现：独立副本切回HEAD旧agent_loop，新增三项测试失败；失败日志
  /tmp/vorto-prompt-protocol-before.log保留。复现证明协议文字冲突，不证明模型质量下降。
- 最终相关261项通过（5.75秒），/tmp/vorto-prompt-protocol-final.log。独立副本141项
  通过（0.90秒），/tmp/vorto-prompt-protocol-isolated.log；路径、退出码与最终源码SHA
  在/tmp/vorto-prompt-protocol-isolated.json。
- 实际SDK+合成服务7项通过（4.89秒），/tmp/vorto-prompt-protocol-sdk.log；使用已有
  临时SDK，测试仅自建临时回环服务并自行清理，未调用真实oMLX或云端模型。
- 本地Python编译、Ruff及diff检查通过；本轮没有新的前端构建/.app/sidecar构建。
  独立源码副本不是正式安装包。浏览器/原生GUI/真实provider模型效果未验收，未发布。
- 用户实际模型配置未变，11500服务未操作；原生失败与暂缓记录保留。没有启用自动
  检查、自动推进或新provider权限；完成状态不能替代任务验收、交接、持久交付或Goal。


## 44. 深色时间线与本地搜索响应兼容（2026-10-08）

### 源码/本地构建

- 时间线硬编码浅色文字/图标背景/详情色改用原主题变量，长标签换行。
- SDK缺失降级路径修复content缺失/null，检测无效响应与不支持的原生工具请求。
- General请求附加日期与搜索说明修正，不暴露工作区，不写history，不改搜索结果。
- 旧源码在独立副本的两个content用例失败（缺失字段KeyError及null不规范），保留
  /tmp/vorto-local-model-content-before.log。初次日期注入导致历史/压缩两项失败，
  改请求级附加后最终192项通过，/tmp/vorto-local-model-fixes.log。
- 前端378项/35文件通过，/tmp/vorto-local-model-ui-tests.log；生产前端构建20.61秒
  通过，/tmp/vorto-local-model-ui-build.log，保留大chunk提示。Python编译/Ruff/diff通过。

### 隔离运行与浏览器

- 独立源码副本192项通过，/tmp/vorto-local-model-isolated.log。源模块字节与当前工作区
  一致，路径/SHA见/tmp/vorto-local-model-isolated.json；不是正式.app/sidecar包。
- SDK+合成回环服务7项通过，/tmp/vorto-local-model-sdk-regression.log。真实provider
  推理未执行，测试自建服务自行关闭。
- QA真实TurnTimeline组件与原App.css：desktop/qa/timeline.html、timeline.tsx，
  仅通过本轮专用Vite 1448运行，已停止；没有调用模型或改用户配置。
- ego-browser TaskSpace109实际主题切换与搜索详情展开完成，finish keep=[]已结束。
  深色icon背景rgb(24,24,24)、文字rgb(186,186,186)；浅色背景rgb(247,247,245)、
  文字rgb(94,94,90)。长搜索词label高度38px、white-space normal，无横向溢出。
  Page.captureScreenshot超时，截图/像素视觉验收未完成；不能改记截图通过。

### 真实环境/原生/发布

- 项目原web_search实际公开检索oMLX official GitHub返回官网、GitHub、benchmark
  三条结果，/tmp/vorto-real-search-probe.txt。仅证明当次搜索后端可用，不证明模型推理
  或资料新鲜度/答案正确。没有使用模型API Key或向真实模型发送请求。
- 用户截图证明界面显示选中本地模型与配置重启提示，不证明原始响应/SDK版本或
  该截图准确异常路径；真实本地模型完整搜索工具回路仍未验收。
- 原生GUI继续暂缓，正式.app/sidecar运行包未构建或替换，未安装、提交或发布。
  原有失败证据保留，TaskLedger/锁/执行池/Goal与自动检查权限边界均未改变。


## 45. SDK入包、真实本地搜索回路与安装替换（2026-10-08）

### 源码与合同

- runtime锁定openai3.14.1/httpx0.28.1及其缺失依赖，移除冻结排除openai规则，
  收集SDK模块和元数据；核心既有版本保留。锁解析/tmp/vorto-desktop-sdk-resolution.json。
- model-capabilities只构造/关闭原本地SDK客户端，固定回环端点，不发送请求；
  缺SDK报错并关闭客户端，不能传地址/Key。原smoke增加构造合同。
- 默认LLMClient保留启动器保存的本地端点/Key；显式空Key本地配置仍不借环境云端Key。
- 第一次新增诊断单测误以为LLMConfig自行生成占位符，51项中1项失败；真实占位符
  由LLMClient构造，修正fixture断言后最终相关95项通过，
  /tmp/vorto-desktop-local-contract.log。Ruff/JS语法/diff通过。
- 最终SDK合成HTTP/SSE7项通过（4.77秒），/tmp/vorto-desktop-local-sdk-contract.log。

### 构建与运行包

- 原内置runtime没有SDK，并明确exclude openai；首次入包后的实际回路仍401，
  保留/tmp/vorto-frozen-local-probe-before-auth-fix.json。保存Key读取/models200且
  模型存在，定位默认客户端覆盖Key；修复后重建。
- 最终新包完整构建/sidecar smoke/Rust release/Developer ID签名通过，
  /tmp/vorto-desktop-local-build.log及target/release/dev-signing-evidence.json。
  PyInstaller可选openai.helpers缺numpy告警保留；没有增加voice模型能力。
- 冻结.app/Contents/Resources/runtime的独立临时Gateway实际读取保存的11500配置，
  原WS agent plan请求经本地Qwen3.6-35B-A3B-4bit调用web_search，收到succeeded与
  实际搜索结果，agent_emit回复https://github.com/jundot/omlx并agent_done。
  /tmp/vorto-frozen-local-probe.json passed=true。仅本测试临时cwd/会话；结束自行停止。
  max_steps2、客户端120秒期限不证明provider硬预算或取消后服务端停止。

### 安装与真实环境

- 已按用户授权替换/Applications/VortoCode.app；先备份并校验旧包到
  /private/tmp/vorto-desktop-backup-ITeK6o/VortoCode.app。保留旧包，未停止其它应用/
  项目/模型服务，也未操作窗口、输入法或原生系统权限。安装前未发现VortoCode GUI进程。
- /tmp/vorto-desktop-install-evidence.json：新GUI二进制SHA
  92dc1e151579005301d3cf8c49334c7bf6ecf412d396e144f5ab16adca0a3e41，runtime树SHA
  00ad2944c15a91faae2eb347c1ce2204c233497a3dd4b7ca5283829d509ed102。
  与最终构建证据一致，designated requirement与旧安装相同。
- 已安装SDK诊断openai3.14.1/httpx0.28.1与Gateway smoke通过（556毫秒），
  /tmp/vorto-desktop-installed-smoke.log；原verify签名通过，
  /tmp/vorto-desktop-installed-signature.log。没有启动原生GUI进行验收。
- profile当前实际配置为11500/Qwen且Key存在；Key只在进程内使用，未改变profile/
  .env/服务设置，未打印到证据。真实搜索链路通过不能替代文件编辑、Shell、视觉、音频、
  长上下文、取消服务端停止、自动检查或Goal验收。

原生GUI继续暂缓，第44节浏览器截图超时仍未完成；本轮磁盘安装已完成，未正式发布/
公证上传/提交/部署，不将安装完成当作原生验收或全能力通过。

## 46. 模型设置交互收敛（2026-10-08）

**基线**：保留第45节安装与真实oMLX验收及更早原生未完成记录。本轮是源码与浏览器隔离验收，
不是已安装Desktop窗口验收，也没有改变用户配置或其它会话。

实际复现：原设置页显示全部供应商/调度字段；本机快捷预设会把自定义端口替换为默认端口；
更换服务地址仍展示旧模型上下文与“已安全保存”的Key提示。浏览器TaskSpace110使用原组件、
非敏感模拟profile，未经真实鉴权或写盘。IAB不可用；截图CDP超时，未获得视觉证据，
不能称为完整产品设计审查。以下是当前DOM/交互检查与合同证据。

1. 进入模型服务设置：成功。新增操作顺序说明，本地模型无需登录；字段字体实际13px，
   当前视口settings-pane无水平溢出；无截图，不证明所有视口或像素质量。
2. 点击oMLX快捷项：成功保留11500与Qwen模型。不是对服务类型的自动识别。
3. 修改为另一地址：上下文显示“当前草稿尚未检测”，旧配置来源不适用于草稿；
   Key提示改为仅无需鉴权才可留空，不显示另一服务Key已保存。
4. Key显示按钮：成功切换用户输入框类型；未读取或显示任何真实Key。
5. 模型名清空：保存禁用，展示填写模型名/获取列表的原因。高级选项初始两项均关闭、可显式展开；
   搜索oMLX仅列模型服务入口。
6. 独立编译副本：使用/tmp/vorto-settings-compiled，模型设置来自生产原组件；
   保存按钮只记录“仅记录保存意图，未写盘或重启”，未调用原生API。不能作为实际保存成功证明。

**源码/测试**：modelSettings.test.ts新增6项合同；36文件/384项前端测试通过，
包括原连接请求隔离测试。git diff --check通过；完整前端tsc/vite构建通过。
日志：/tmp/vorto-settings-tests.log、/tmp/vorto-settings-build.log、/tmp/vorto-settings-fixture-build.log。

**运行包/真实环境/发布**：本轮没有构建新的Tauri运行包或替换/验收已安装包；
第45节通过仅属于当时包与真实搜索链路。没有新的provider调用或权限。
原生与视觉截图验收仍未完成，未正式发布。独立页面服务器和本轮浏览器空间验收后关闭，
不触碰Codex、oMLX、协调聊天或其它项目进程。下一轮按PRD第46节显式诊断合同推进。

## 47. 工具循环诊断与联网调用截断（2026-10-08）

**原环境证据**：当前运行9071/9076是/Applications/VortoCode.app及其General Gateway59928；
保留11500 oMLX进程30789。只读session_events与audit确认rid
7e310247-0c19-4fc4-bae7-ee83c47298cc循环检索，最终61次搜索/45次fetch/1次产物列表。
最后模型工具格式错误（unrecoverable tool call）后agent_done，不是任务成功或Goal完成。
首次检查为55次搜索/41次fetch；继续增长不是UI动画重复。现有同参数提醒没有派发截断，
变化关键词与Build自动续跑使单次请求执行过多工具。

**现场处理**：没有停止进程。拟按原Gateway取消前再次读到原请求已终态，未发送取消。
最初系统Python没有websockets，未建立连接；改用既有miniforge环境复核后只报告已终态。
未连接其它会话、未改真实用户配置或权限，不证明provider调用即时停止。

**源码/测试**：新增单个MainAgent用户回合联网派发上限，跨原Build续跑累计；
原生/提示式工具共用准入，批次后缀不执行、不写入历史。到限原无工具收尾，
不自动继续或借空回复升级模式；失败调用也消耗额度。9项新增合同，原相关228项测试通过；
补入批次与配置合同后最终19项定向回归通过；ruff与diff检查通过。
日志/tmp/vorto-web-loop-contract.log、/tmp/vorto-web-loop-final-contract.log、/tmp/vorto-web-loop-sdk.log；
真实SDK7项HTTP/SSE测试通过，属于合成回环端点。

**构建/隔离运行包**：/tmp/vorto-web-loop-bundle.log记录sidecar/前端/Rust/Tauri与原Developer ID签名通过，
无公证上传。包含第46节设置优化。独立冻结Gateway的合成循环模型只派发2次web_fetch，
随后一次无工具收尾；总3次模拟模型请求，输出“联网检索已停止，资料不足，尚未核实。”并agent_done。
web_fetch使用拒绝SSRF的回环URL，不访问外部网站。证据/tmp/vorto-frozen-loop-evidence.json。
首次合成夹具空Key被原Gateway入口拒绝，/tmp/vorto-frozen-loop-initial-evidence.json保留未通过；
仅夹具改用fixture-nonsecret，未改产品鉴权。不是实际oMLX循环96次后的同上下文复验。

**真实本机provider**：隔离新冻结包读取已有11500配置，Key只在子进程环境中，未打印或写盘。
联网额度1，实际web_search成功并回复官方GitHub链接，证据/tmp/vorto-web-loop-local-probe.json。
正常搜索路径真实通过；无限模型行为的截断由上述合成循环证明，不声称已覆盖所有真实模型行为。
不是token/时间/费用硬预算，不证明子Agent链级额度或provider硬取消。

**浏览器/原生/发布**：第46节原设置组件的浏览器及独立编译检查保留；本轮UI来自同一源码并随包构建。
没有新的截图或原生窗口/焦点/IME/VoiceOver/Hook验收，原记录仍未完成。未正式发布。
安装替换沿用既有授权，同签名备份核验；当前运行进程未被重启，新代码需退出并重新打开应用后加载。

安装最终结果：/Applications/VortoCode.app；旧包备份/private/tmp/vorto-desktop-backup-2fnjar/VortoCode.app，
安装证据/tmp/vorto-web-loop-install-evidence.json（不覆盖第45节安装记录）。新应用SHA256
5fc0fdc4b21814193966133bf491e99870c06fd2d16edd99b58277f127733a31，runtime树SHA256
65a69b461287182f42a3f71fcf77021b0d1de756a0c0afdbb04f6dfc312d234c。
已安装包SDK诊断与sidecar smoke通过543ms；签名/指定要求/构建哈希复核通过，日志
/tmp/vorto-web-loop-installed-smoke.log、/tmp/vorto-web-loop-installed-signature.log。
本轮原生GUI未启动/重启，用户现有运行实例仍是此前已加载的版本。


## 48. MCP / Skill 调用链（2026-10-08，UTC运行证据为10-09）

**源码与测试**：首轮3项合同失败，日志/tmp/vorto-extension-before.log；修复连接执行路由、完整参数schema、
失败事件、Skill来源/相对路径、安全保存及加载边界。中途夹具缺transport造成1失败/211通过，
补齐真实客户端属性后复验，未改产品合同迁就夹具。最终227项通过/1项既有Starlette警告，
/tmp/vorto-extension-contract.log；Ruff和diff检查通过。

**构建**：/tmp/vorto-extension-bundle.log记录sidecar SDK smoke、tsc/vite、Rust/Tauri及原Developer ID
Ningbo Vorto Technology Co., Ltd (8UTMY2WR9H)签名通过。没有公证上传或正式发布；
原可选voice_helpers缺numpy警告不变，不声称音频验收。

**独立运行包**：/tmp/vorto-extension-package-initial-evidence.json保留项目local作用域拒绝MCP的失败尝试。
没有放宽权限；新/tmp/vorto-extension-package-evidence.json分别用General允许作用域与项目Skill作用域，
MCP整数/required合同、成功及isError失败事件通过；use_skill后实际read_file引用资料并输出资源确认。
模型为合成SSE/JSON端点，MCP是自有旧REST夹具，不是标准第三方互操作证据。

**真实本机模型**：/tmp/vorto-extension-local-model-evidence.json passed=true。
隔离新冻结Gateway消费已有http://127.0.0.1:11500/v1配置，Qwen3.6-35B-A3B-4bit成功调用一次
mcp__fixture__count，count为整数2，最后回复“工具调用已成功完成。”。Key只在子进程环境，未打印或保存。
步骤上限限制模型轮次，不能当成MCP批次/provider token或费用硬预算。MCP服务仍为自有回环REST夹具，
没有第三方服务、真实模型Skill引用或新provider授权。所有隔离子进程结束并回收，不停止原oMLX/Desktop。

**安装**：沿用既有替换授权，/Applications/VortoCode.app已更新；旧包备份
/private/tmp/vorto-desktop-backup-YcBK59/VortoCode.app。/tmp/vorto-extension-install-evidence.json固定安装哈希：
app 48336a5c35f48b07adb75d8ac27d5779fd627590ebb8007056426a2fc4affba0，
runtime b75b8416aa9ed6f24e6a1d285105ce5027e3f57a0b0942949bd9325a2d34e44f。
已安装SDK诊断与隔离sidecar健康通过549ms，日志/tmp/vorto-extension-installed-smoke.log。
首次沙箱签名复核报aiohttp扩展invalid signature，保留/tmp/vorto-extension-installed-signature.log；
只读提升权限复核通过/tmp/vorto-extension-installed-signature-escalated.log，未重新签名或改包。
安装时源/目标/备份哈希及指定签名要求核验通过。

**浏览器/原生/发布**：本轮没有新增浏览器或原生UI验收，第46节浏览器记录及此前未完成断言保留。
未启动、停止或重启现有GUI，会话保持运行；用户退出并重新打开后才加载新代码。未正式发布。
当前HTTP不是标准Streamable HTTP；Desktop项目local边界拒绝外部MCP，General不加载仓库Skill，
默认opt-in未开启；配置可见不代表当前会话可用。多服务器同名路由、显式连接/重试入口、Skill重载仍待下一轮。
自动检查、自动推进及生命周期/Goal边界均未改变。


## 49. MCP 多服务同名工具与刷新保护（2026-10-08）

**源码/合同测试**：复现两个服务同名覆盖及刷新先删后读问题；首次新增测试3失败/1通过，
/tmp/vorto-mcp-identity-before.log。固定完整服务/工具身份，远端调用保留原名；无歧义裸名称兼容，
权限规则同时保留原名和完整名，调用频率归一。刷新先校验后发布，HTTP错误不作为空目录，
注销等待前移除连接所有权，拒绝迟到发布。新增17项合同，最终相关224项通过，
/tmp/vorto-mcp-identity-tests.log；Ruff及diff检查通过。
中途客户端夹具缺transport的1失败/52通过已修正为实际属性，并补完整回归；没有更改产品迁就夹具。

**构建记录**：构建期间补齐目录和注销竞态后重新构建，前两次构建不作为最终源码交付证明。
日志/tmp/vorto-mcp-identity-bundle.log、/tmp/vorto-mcp-identity-final-bundle.log保留；
最终交付构建日志/tmp/vorto-mcp-identity-delivery-bundle.log。
准备临时脚本时系统Python字节码缓存写权限错误，改为只在内存编译，未调整系统权限；
一次准备命令从desktop目录访问根目录相对路径失败，无源码写入，后续仅更新文档计数。

**验收边界**：当前仍是自有旧REST MCP适配器，不能代表标准Streamable HTTP互操作。
本轮没有新的真实oMLX/第三方provider或浏览器验收；第48节本机模型通过仅保留为此前证据。
不改真实MCP配置、不启用默认opt-in、不扩展项目local能力；同连接并发刷新序号/版本绑定尚未建立。
没有原生启动/焦点/输入法/VoiceOver/Hook验收，原未完成断言保留，未公证上传或正式发布。
任务done、验收、交接、持久交付和Goal边界不变。


**最终构建/隔离运行**：最终同Developer ID完整包构建通过，包含最后注销竞态修复，未公证上传。
/tmp/vorto-mcp-identity-package-evidence.json passed=true；General隔离Gateway连接两个均提供count的
自有回环REST服务，实际端点顺序/alpha/tools/call、/beta/tools/call、/alpha/tools/call，
三次远端name均为count。两服务成功事件与第三次isError失败事件正确，schema整数/必填保留；
项目作用域Skill use_skill/read_file及资源标记回归通过。模型为合成JSON/SSE，未访问第三方。
独立子进程及夹具均结束回收，不触碰Codex、原Desktop/oMLX或协调会话。

**安装/健康**：/Applications/VortoCode.app更新，旧包备份/private/tmp/vorto-desktop-backup-ZOwXZf/VortoCode.app；
/tmp/vorto-mcp-identity-install-evidence.json记录app SHA256 61b653903793becafb1a9b1e7e57882f4e0c57a1a269693ecc942787e45b4de5，
runtime树SHA256 e7628a6eb04256dc193d86dae68027308bb3949b38948621ab70c99be4449add。安装前后及备份哈希、原指定签名要求通过；
已安装签名复核/tmp/vorto-mcp-identity-installed-signature.log通过，SDK诊断/sidecar健康
/tmp/vorto-mcp-identity-installed-smoke.log通过。未自动启动/停止/重启原生应用，用户退出重开才加载新代码。
本轮无新增真实provider、浏览器或原生GUI验收，未正式发布。


## 50. 标准HTTP工具子集与真实本机模型（2026-10-08）

**源码/合同测试**：新增src/tools/mcp_http.py，经原MCP工厂显式选择2026-07-28 Streamable HTTP。
旧省略protocol配置继续legacy_rest，权限/完整身份/原执行器不变。新增28项HTTP合同，相关252项通过，
/tmp/vorto-mcp-http-contract.log；Ruff及diff检查通过。覆盖JSON/SSE/CR、分页/预算、身份/错误、
超时/取消、参数头安全编码、非法schema排除、原权限和不支持版本在请求前拒绝。
代码及接口范围见MCP_HTTP.md、PRD第50节；没有完整MCP符合性声明。

**本地构建**：/tmp/vorto-mcp-http-bundle.log记录sidecar SDK、tsc/vite、Rust/Tauri及原Developer ID
Ningbo Vorto Technology Co., Ltd (8UTMY2WR9H)签名通过，未公证上传。既有可选音频依赖和前端chunk
大小告警未改变，不声称对应功能或性能验收。

**原包复现/独立运行包**：/tmp/vorto-mcp-http-before-evidence.json用旧安装包隔离实例复核同一标准端点，
passed=false，无MCP工具调用；原Skill用法仍通过。失败证据保留，不启动原生窗口或改用户会话。
/tmp/vorto-mcp-http-package-evidence.json用新冻结包与原Gateway WebSocket客户端协议，passed=true。
General作用域连接两个同名count服务：alpha返回JSON、beta返回SSE，每个目录两页；实际工具端点
顺序/alpha、/beta、/alpha，原name=count，第三次isError呈现failed，整数/required完整。
夹具检查协议/方法/名称/Count头与请求正文一致；项目Skill的use_skill/read_file和资源标记回归通过。
模型为自有合成JSON/SSE，不代表第三方MCP符合性或浏览器/原生GUI验收。

**真实oMLX**：/tmp/vorto-mcp-http-local-model-evidence.json passed=true，新冻结包隔离General会话
读取已有http://127.0.0.1:11500/v1配置，Qwen3.6-35B-A3B-4bit成功调用一次mcp__beta__count，
count为整数2，远端是自有2026标准SSE端点，最终回复“操作已成功完成。”。Key仅在子进程环境，不打印/保存。
单模型工具轮次及禁自动Build续跑用于有界探测，不是批次/provider token/费用硬预算。
不是第三方服务、真实模型Skill、原生Desktop配置保存或实际窗口验收；没新增provider授权。
各探测子进程和夹具回收，未停止原oMLX、Codex、协调或其它项目进程。

**安装包**：沿用已有替换授权更新/Applications/VortoCode.app，旧包备份/private/tmp/vorto-desktop-backup-dfekA2/VortoCode.app。
/tmp/vorto-mcp-http-install-evidence.json固定app SHA256 e56af5fffd0abab249059922864f50592ab64f7854aef149a8363a93f2360d95，
runtime树SHA256 6ac9216af99a58a25021b0f78e1aa89c212a07c97be9f51738008b5ebebd2e00。源/目标/备份及原指定签名要求核验通过；
/tmp/vorto-mcp-http-installed-signature.log复核通过，/tmp/vorto-mcp-http-installed-smoke.log记录
已安装SDK与隔离sidecar健康通过。不自动启动/停止/重启原生应用，退出重开后才加载新代码。

**未验收与下一轮**：本轮无新的浏览器/原生GUI验收，原失败断言与未完成记录保留，未正式发布。
仅支持明确2026版本tools/list及tools/call，不自动降级/重试；2025标准会话及完整版本协商、
资源/prompts、订阅、MRTR、sampling/elicitation/roots和Desktop连接状态/重试入口均未实现。
项目local仍拒绝外部MCP，General无仓库Skill；真实配置/默认opt-in/自动检查推进均未修改。
下一轮按PRD第50节实现旧标准显式兼容及状态恢复入口；done、验收、交接、持久交付与Goal仍分离。


## 51. 旧标准HTTP、资源提示与Desktop连接重试（2026-10-08）

**源码/合同测试**：原mcp_http新增显式2025-03-26/2025-06-18/2025-11-25握手及有限协商，
Session-Id/initialized/DELETE及取消通知，过期调用不重放。旧server声明资源/提示时追加原权限门内
通用列表/读取工具，不自动采样、订阅或执行返回的提示。mcp_session领域管理原agent接入/观察/重试，
旧工具失败保留、自有目录替换去重、迟到/忙拒绝、并发连接拒绝；配置范围/大小/16服务/30秒有界。
原Gateway新增两条精确会话状态/手动连接端点，项目local继续拒绝，General其它项目API仍受限。
最终263项后端相关回归通过，/tmp/vorto-mcp-session-contract.log；Ruff/diff通过。
资源测试夹具最初误把资源请求当工具请求读arguments而失败，修正夹具后回归通过；不是产品重放/回退。

**前端/浏览器**：Desktop原插件入口增加McpConnectionCard；展示当前会话状态、数量、时间、失败与重试。
请求按client/session/connection代次隔离，原客户端入口复用；没有持久化真实MCP配置或默认打开opt-in。
384项原前端测试通过，tsc通过，/tmp/vorto-mcp-desktop-tests.log；独立编译原组件
/tmp/vorto-mcp-desktop-fixture-build.log。浏览器ego-browser空间111验证首次失败可见、手动重试成功、
在途切会话后旧响应忽略、local受限按钮禁用；/tmp/vorto-mcp-browser-evidence.json。
一次旧元素引用失效，在同一空间刷新snapshot后继续完成，未新建空间规避错误；最终finish关闭空间，
本轮回环页面服务器停止。不以DOM/夹具验收替代实际Desktop保存或原生窗口，未做视觉截图验收。
最初准备fixture使用错误相对路径未写入，按desktop工作目录修正后编译/浏览器验收通过。

**构建/冻结包验收**：初次/tmp/vorto-mcp-desktop-bundle.log构建通过，但运行API被原General范围门
拒绝，/tmp/vorto-mcp-desktop-package-evidence.json保留passed=false；进一步
/tmp/vorto-mcp-desktop-package-diagnostic.json固定workspace_required及无MCP请求。
仅新增两条精确范围合同后重建，最终/tmp/vorto-mcp-desktop-final-bundle.log通过原Developer ID签名，
未公证上传。/tmp/vorto-mcp-desktop-final-package-evidence.json passed=true：
原Gateway WebSocket建立会话后实际POST两次连接/重试、GET状态，目录保持10个工具无重复；
alpha2025-03-26 JSON及beta2025-11-25 SSE工具成功/失败状态正确，resources/read和prompts/get成功，
项目Skill use_skill/read_file回归通过。两服务声明资源/提示，工具数包含通用工具，不是10个资源。
模型/服务均为自有合成回环夹具；不是第三方MCP完整符合性或真实浏览器HTTP接入原生应用的证明。

**真实本机模型**：/tmp/vorto-mcp-desktop-local-model-evidence.json passed=true。
新冻结包读取已有11500 oMLX配置，手动连接/重试两次后Qwen3.6-35B-A3B-4bit仅调用一次
mcp__beta__count，整数count=2，旧版SSE自有服务结果正确，最后回复“操作已成功完成。”。
Key只在隔离子进程环境，不打印/存盘。单工具轮次/禁自动Build继续仅为本次有限探测，
不是provider硬预算或取消费用证明；真实模型资源/提示、第三方服务尚未验收。
本轮隔离子进程及服务均回收，不触碰原oMLX、Codex、协调或其它项目进程。

**安装/签名/健康**：沿用已有授权更新/Applications/VortoCode.app，旧包备份/private/tmp/vorto-desktop-backup-bBbxNN/VortoCode.app。
/tmp/vorto-mcp-desktop-install-evidence.json固定app SHA256 93c53319d49aedbc04abdeb4b2fda71e0e9c3713cec238e4993f85bf15dce891，
runtime树SHA256 78c1f2b4f36cf95cbd81780b48a3289e78d9031abb1bf730b89a65083ff97090。源/目标/备份及原签名指定要求核验通过；
/tmp/vorto-mcp-desktop-installed-signature.log复核通过，/tmp/vorto-mcp-desktop-installed-smoke.log
记录已安装SDK/隔离sidecar健康通过。没有启动、停止或重启原生GUI，新代码需退出重开后加载。

**未完成/发布边界**：原生GUI/焦点/输入法/Spaces/VoiceOver/Hook仍未验收，旧失败断言保留，未正式发布。
不是完整MCP符合性；资源-only目录装配、2026资源提示发现、2024双端点SSE、断流恢复、
订阅、sampling/elicitation/roots/MRTR及工具版本绑定仍待合同/实现/验收。
未扩大provider或项目local权限，未启用自动模型检查/推进，done、验收、交接、持久交付和Goal仍分离。


## 52. 资源-only装配与重试观察真实性（2026-10-09）

**源码/合同测试**：仅resources或prompts的旧标准服务不再被强制请求tools/list，
只装配初始化已声明能力。未声明任何相关能力是成功空目录，已声明tools的失败仍上抛。
失败尝试记录attempted_at，保留旧目录observed_at；新连接发布后取消不恢复旧观察。
候选清理异常/超时不能把连接中标记永久锁住，finally释放；未确认清理不证明远端停止。
/tmp/vorto-mcp-feature-only-before.log保留修复前3项失败；新增5项合同后，
/tmp/vorto-mcp-feature-only-contract.log记录268项相关测试通过。最终Ruff和git diff --check通过。
原目录、执行池、权限与会话入口复用，未新增自动重试、模型动作或数据库。

**本地构建**：首次/tmp/vorto-mcp-feature-only-bundle.log成功，但最终清理修复在其构建期间完成，
因此不作为最终源码包证据。停止源码改动后重建，/tmp/vorto-mcp-feature-only-final-bundle.log
记录最终签名包成功：冻结Python、SDK/隔离健康、tsc/Vite、Rust及原Developer ID签名通过。
既有大chunk和非本轮voice_helpers依赖收集警告保留；未公证上传。

**独立运行包**：/tmp/vorto-mcp-feature-only-package-evidence.json passed=true。
使用最终冻结runtime、自有临时工作区与合成模型，原WebSocket建立General会话后通过原REST端点
手动连接/重试共4次，GET状态始终为4个通用工具且无重复。alpha是2025-03-26 JSON资源-only服务，
beta是2025-11-25 SSE提示-only服务；resources/read和prompts/get实际执行成功，RPC记录无tools/list。
随后隔离项目会话use_skill/read_file回归成功。模型/服务均为回环夹具，未访问真实provider。
本轮只回收自有子进程与服务器，未停止oMLX、Codex、协调或其它项目进程。

**安装/健康**：沿用已有授权，备份旧包至/private/tmp/vorto-desktop-backup-7FaqNk/VortoCode.app，
再更新/Applications/VortoCode.app。/tmp/vorto-mcp-feature-only-install-evidence.json固定
app SHA256 c6305492cacb89c6b5dd8cd0bd42186c6353d84f53975eb997065ef00196b255，
runtime树SHA256 d17e991186f8fbc11f89c0ff45bc8717b1e593ff9c0c93391d3e41815cc41291。
源/目标/备份摘要及原签名指定要求核验通过；/tmp/vorto-mcp-feature-only-installed-signature.log
复核通过，/tmp/vorto-mcp-feature-only-installed-smoke.log记录已安装隔离sidecar健康547ms通过。
没有启动、停止、重启或绑定原生GUI；当前已打开应用需用户以后退出重开才加载新代码。

**验收/发布边界**：本轮无前端源码改动或新浏览器验收，第51节原组件浏览器证据仅保留原范围。
本轮真实provider未验收，第51节oMLX单工具调用记录保留，不扩大成真实模型资源/提示验收。
原生GUI、焦点、输入法、Spaces、VoiceOver、Hook继续未验收，旧失败断言保留；未正式发布。
2026资源/提示发现与目录版本/调用绑定是下一切片；sampling、elicitation、roots、订阅、
断流恢复、2024 SSE及完整MCP符合性仍待独立合同。未扩大provider或项目local权限，
未启用自动检查/推进；done、验收、交接、持久交付与Goal仍分离。


## 53. 初始化失败清理与取消恢复（2026-10-09）

**源码/合同**：复核第52节后发现manager移交前失败清理尚无独立超时，清理异常会掩盖
原初始化失败；候选清理期间取消也未标明清理未确认。/tmp/vorto-mcp-cleanup-before.log
保留4项修复前失败，24项通过。在原mcp_tools/mcp_session实现独立5秒失败清理额度、
原始异常保留、取消继续传播及固定无敏感信息note投影。会话finally释放在途门；
清理未确认不证明远端停止，asyncio超时不能强行终止非合作式阻塞。原目录/会话/权限复用。
新增5项合同覆盖清理错误/超时/取消、会话取消未确认及脱敏。首次广泛回归沙箱不能绑定回环：
/tmp/vorto-mcp-cleanup-contract.log保留245通过/27项fixture错误；获准回环后
/tmp/vorto-mcp-cleanup-final-contract.log最终273项通过，Ruff/git diff --check通过。

**本地构建**：/tmp/vorto-mcp-cleanup-bundle.log记录冻结Python、SDK smoke、tsc/Vite、
Rust及原Developer ID签名构建通过，未公证上传。既有chunk/voice_helpers收集警告未改记消失。
临时probe的系统Python py_compile尝试被缓存目录权限拒绝，改用AST只读语法核验通过；
不是产品构建失败，也没有扩大缓存写权限。

**独立运行包**：/tmp/vorto-mcp-cleanup-package-evidence.json passed=true，使用最终冻结runtime、
自有临时工作区及回环合成模型/服务。原Gateway WebSocket/REST建立会话并连接/重试，
资源-only JSON和提示-only SSE目录保持4个工具无重复；主动返回不支持版本使重连HTTP409，
GET核验旧目录/工具数/observed_at不变、attempted_at存在，随后手动重试成功。成功连接共5次。
实际resources/read、prompts/get及项目Skill use_skill/read_file均成功，RPC无tools/list。
清理异常/超时/取消由合同测试覆盖；包验收验证服务错误及恢复，不冒称包中注入清理故障。
只回收自有子进程和服务器，未停止oMLX、Codex、协调聊天或其它项目。

**安装/签名/健康**：沿用已有授权，备份/private/tmp/vorto-desktop-backup-LmDA2A/VortoCode.app，
再更新/Applications/VortoCode.app。/tmp/vorto-mcp-cleanup-install-evidence.json固定
app SHA256 43e69a21bff0fb32be251c4ad0c4265ba7371f5f272fbcb5c890c56086c36225，
runtime树SHA256 65039643ae3d1ef4f55015e7c777f88cb422d629fe8e4f4bce18bebed561c85c。
原指定签名要求、源/目标/备份摘要核验通过；/tmp/vorto-mcp-cleanup-installed-signature.log
签名复核通过；/tmp/vorto-mcp-cleanup-installed-smoke.log记录已安装sidecar健康549ms通过。
未启动/停止/重启原生GUI，当前已打开应用仍需用户以后退出重开加载新代码。

**验收/发布边界**：本轮无前端源码改动或新浏览器/真实provider验收。第51节原组件浏览器、
oMLX单工具记录仅保留原范围，不当作本轮真实模型资源/提示或清理故障验收。
原生GUI/焦点/输入法/Spaces/VoiceOver/Hook仍未验收，旧失败断言保留；未正式发布。
下一阶段为2026资源/提示发现和目录版本/调用绑定，完整MCP仍未达成。
未扩大provider/项目local权限、自动检查/推进；done、验收、交接、持久交付和Goal分离。


## 54. 简单查询收敛与macOS关闭到Dock（2026-10-09）

**问题来源/源码**：用户天气截图显示3次搜索+1次读取，真实General会话历史核对为
西雅图旅游、宁波百科/旅行及杜鹃花等无关搜索结果，最后wttr.in/Ningbo读取成功。
该记录已经收尾，不能称无限循环或Build重复。原SYSTEM_TEMPLATE加入简单查询收敛、
城市/主题/日期检查与来源链接规则，改写一次后转已知数据源或说明不足；多来源研究不套用此建议。
这是软指导，原运行时派发额度不变，搜索后端相关性未解决。
macOS主窗口CloseRequested阻止销毁并minimize，失败保留窗口；原Dock Reopen/单实例恢复复用，
Quit/Command-Q仍使用原Exit清理。页面加载和启动4秒兜底尊重最小化，不新增后台动作。
其它平台关闭行为不变。截图Markdown表格原文显示已定位：原MarkdownMessage没有表格解析，
本轮未改渲染器，保留下一切片，不误记已修复。

**单元/合同测试**：/tmp/vorto-search-dock-contract.log记录170项后端相关测试通过，Ruff/diff通过。
/tmp/vorto-search-dock-rust.log保留77通过/4项回环绑定PermissionDenied失败；获准自有回环后
/tmp/vorto-search-dock-final-rust.log记录81项Rust测试通过。这不是关闭/Dock实际点击验收。

**构建与隔离包**：/tmp/vorto-search-dock-bundle.log记录冻结Python、SDK smoke、tsc/Vite、
Rust及原Developer ID签名通过，未公证上传；既有chunk/voice_helpers收集警告保留。
/tmp/vorto-search-dock-package-evidence.json passed=true，原Gateway/冻结runtime的实际模型请求
包含新简单查询指导；合成回环模型/服务验证MCP资源/提示、失败重连保留观察后恢复、Skill读取回归。
这是原运行包入口验证，没有启动原生窗口，不证明Dock呈现或前端表格已修复。

**真实本机模型**：/tmp/vorto-search-local-evidence.json passed=true，仅一次隔离查询
“宁波今天天气怎么样？简要回答并提供来源。”；读取已有11500 oMLX/Qwen3.6-35B-A3B-4bit配置，
Key只在子进程环境，不打印/存盘。实际2次搜索+1次web_fetch，最终回复包含wttr.in/Ningbo来源链接。
两次搜索依然无关，最后来源读取取得数据。验收仅为该次派发次数/收尾/链接，不证明相关性修复、
所有简单查询都会收敛或气象准确性。3次web派发、3步、禁自动Build继续及100秒观察限额只约束
此隔离探测，不是provider费用/token硬预算或远端费用停止证明；没有新provider权限。
自有runtime已回收，原Desktop/oMLX/Codex/协调及其它项目进程未停止。

**安装/签名/健康**：沿用授权，备份/private/tmp/vorto-desktop-backup-dnXeOz/VortoCode.app，
更新/Applications/VortoCode.app。/tmp/vorto-search-dock-install-evidence.json固定
app SHA256 cd18ead8bd9352fc6701e3b11eec4900dd7e8c96e953499a58223c5748a2da37，
runtime树SHA256 4f30e45b56344d5e2b9e122909703d35a42753dc94ae214ec717e62408ade45d。
源/目标/备份与原签名要求通过，/tmp/vorto-search-dock-installed-signature.log复核通过，
/tmp/vorto-search-dock-installed-smoke.log记录已安装sidecar健康552ms通过。
沿已授权入口整理，仅刷新VortoCode主注册，不删除包/备份，不处理Vorto Write文件。
没有自动启动、退出或重启GUI，新关闭行为需用户明确退出再打开后加载。

**未验收/发布**：原生Dock最小化/恢复、关闭失败、Quit清理及启动加载期间交互仍未验收，
焦点/输入法/Spaces/VoiceOver/Hook旧失败及未完成记录保留。无新浏览器验收，未正式发布。
下一切片表格安全渲染与搜索相关性；MCP未完成合同继续保留。未开启自动检查/推进；
done、验收、交接、持久交付和Goal保持分离。


## 55. 日常使用与交付收敛（2026-10-09）

**范围/源码**：用户明确选择稳定日常使用与交付，本轮暂停扩MCP/provider/自动化能力。
README/ROADMAP与DESKTOP_DAILY_BASELINE.md统一主链路、唯一安装入口、固定命令及已知边界；
保留原TaskLedger/执行池/生命周期及全部无关工作区改动，未提交、合并或正式发布。
原MarkdownMessage补表格子集、安全行内内容、32列限制和窄宽滚动，没有新增渲染数据库或库。
最终复核非法代码围栏触发原同步死循环，/tmp/vorto-daily-malformed-before.log保留4秒未返回
及只终止自有探测进程组证据；统一段落/围栏准入后普通文字不再卡住。

**测试**：新增6项回复合同，最终/tmp/vorto-daily-final-frontend.log记录390项前端通过，tsc通过。
首次389项通过后tsc拒绝Array.at，改用既有编译目标支持的下标，没有扩大编译目标。
新增6项安装合同覆盖成功备份、首次安装、保存失败、暂存损坏、发布后核验失败回退、身份变化拒绝；
/tmp/vorto-daily-final-delivery.log全通过，脚本语法/diff通过。回退失败保留资料路径已实现，
本轮未注入真实/模拟回退失败，不把该分支当作已验收。后端/Rust未改，沿第54节保留原范围。

**浏览器**：应用ego-browser技能，在空间112运行原组件编译夹具，表格/表头/数值对齐正常，
代码块原文、HTML文字/危险链接不执行、可聚焦区域及280px容器横向滚动通过，
/tmp/vorto-daily-browser-evidence.json passed=true。首次420px表格正好容纳，断言错误要求
发生溢出而失败；/tmp/vorto-daily-browser-first-check.json保留。在同一空间调整夹具容器后通过，
不是产品修复或另起空间规避。最终关闭自有空间/回环服务器。未做截图/原生视觉验收，
表格浏览器证据早于非法围栏最后一行修复，后者由390项合同覆盖，不扩大原浏览器范围。

**构建/隔离运行**：/tmp/vorto-daily-bundle.log与首次固定安装成功，但最终围栏修复之后
重新构建，最终/tmp/vorto-daily-final-bundle.log通过冻结Python、SDK健康、tsc/Vite、Rust和
原Developer ID签名，未公证上传；既有chunk/voice_helpers收集警告保留。
本轮隔离运行是安装包sidecar健康，未新做MCP/真实provider推理验收；第54节原运行包及
oMLX一次2搜索/1读取记录保留原范围，不当作搜索相关性修复或本轮全部端侧验收。

**固定交付**：原仓库安装命令改用install-app事务函数，先核验备份、核验暂存，再整包替换/核验，
失败回退；install:built-signed复用构建签名证据，无需会话临时脚本。独立健康失败保留备份并报错，
不声称自动回退。首次真实安装日志/tmp/vorto-daily-install.log/证据保留；最终通过仓库同一命令
再次安装，/tmp/vorto-daily-final-install.log、/tmp/vorto-daily-final-install-evidence.json固定
备份/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-59xseL/VortoCode.app，
app SHA256 c277350d1ad7fe8d8ea43ce888c9772848addc25bb6729f2418194db9f2590ce，
runtime树SHA256 0c5ed420d908dde1ae15f6beeb927f8c9d739b6cd55bf3c95b68758dfa9435b0。
已安装sidecar健康553ms通过，/tmp/vorto-daily-final-installed-signature.log签名复核通过。
沿已有入口整理授权只重登VortoCode主入口，/tmp/vorto-daily-final-entry-evidence.json
核验系统仅登记/Applications/VortoCode.app；未删除构建包、回退备份或其它项目文件。
未启动/关闭/重启原生GUI，用户需要⌘Q退出旧实例再开，新关闭按钮是最小化。

**未验收/发布**：原生Dock/关闭/恢复、输入法/Spaces/VoiceOver/Hook及旧失败继续保留；未正式发布。
搜索相关性依然有偏题、完整MCP未完成、真实模型资源/提示未验收；后续只优先主链路复现阻塞。
无自动模型检查/推进或provider权限扩展；done、验收、交接、持久交付和Goal分离。


## 56. 可见版本与构建身份（2026-10-09）

**源码/测试**：Desktop版本统一0.1.1，npm/Tauri/Cargo与对应锁文件一致。原顶栏BuildVersion
显示版本/源码短摘要，原设置常规显示完整构建标识/本地化时间和⌘Q重开提示；设置搜索包含版本。
Vite编译常量和build-info.json来自同一次createBuildInfo，只读取限定产品输入，根.env和用户配置
不纳入。时间与源码摘要区分不同源码/相同源码重建，不冒充Git提交；签名证据保留同一元数据，
构建完成复核期间未变源码，安装拒绝过期/缺版本证据。不是原子构建快照或全部动态依赖指纹。
/tmp/vorto-version-frontend.log记录391项通过，/tmp/vorto-version-delivery.log记录7项通过：
原6项安装合同加1项多断言生成合同（确定性、时间变更、未提交改动、根.env排除、版本失配拒绝）。
/tmp/vorto-version-tsc.log类型检查通过，脚本语法/diff通过；没有改协议/Python/provider模型版本。

**浏览器**：ego-browser空间113使用原BuildVersion编译夹具验证顶栏短信息、完整tooltip、设置详情
及更新提示，/tmp/vorto-version-browser-evidence.json passed=true。夹具标识是
20261009T060305580Z-ca6d3f6b；它与产品包源码摘要相同，但独立编译时间不同，不混为安装包身份。
空间/回环服务器已关闭，无模型请求/真实会话修改，不是原生窗口或整个设置入口交互验收。

**本地构建/安装**：/tmp/vorto-version-bundle.log通过冻结Python/SDK健康、tsc/Vite、Rust与
原Developer ID签名，构建完成源码摘要一致性门通过，未公证上传；既有构建警告保留。
产品包身份 **0.1.1 / 20261009T060241213Z-ca6d3f6b**，源码摘要
ca6d3f6b081d2c94792f2840f0ae13a3aa31bcba17cd3b4fa651498a3bc03131。
dist/build-info.json与签名证据buildInfo一致，实际编译JS包含同一标识，已安装Info.plist为0.1.1。
沿用授权，通过仓库install:built-signed备份/核验/替换，/tmp/vorto-version-install.log及
/tmp/vorto-version-install-evidence.json固定备份
/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-jYE77Z/VortoCode.app，
app SHA256 fbb241ba6e73a8e5264c6e3239f18593f0595b192dd4dee7c635143bd14dad78，
runtime树SHA256 1cb2d4e681e75b1e2b4917789da0fb56bcf91450081be258cb3903ceafd3fc20。
安装包隔离sidecar健康547ms通过；/tmp/vorto-version-installed-signature.log复核通过。
入口整理授权保持，/tmp/vorto-version-entry-evidence.json核验仅/Applications/VortoCode.app主注册，
不删除构建/备份/其它项目文件，不自动启动、退出或重启原生GUI。

**未验收/发布**：当前旧窗口不会随磁盘更新改成新版本，需要用户⌘Q重开加载。原生版本显示、
Dock/焦点/输入法/Spaces/VoiceOver/Hook继续未验收，旧失败保留；未正式发布。
本轮无新真实provider或MCP调用验收，既有证据保留原范围；不启用自动模型检查/推进或新权限。
done、验收、交接、持久交付和Goal分离，继续按第55节收敛主链路。

## 57. Composer与运行信息验收（2026-10-09）

**源码**：原App/Composer菜单、ContextStatus/SessionInfo、Inspector信息页接入，复用原
client、plan、activities、prompt queue和sessions；dashboard补compact_enabled投影。
原时间线/确认卡/权限门保留。effort明确默认不可调，手动压缩未接通，没有假选择或模型参数。
加号真实用途改为可解释菜单，`/`同菜单动作，未知命令打开菜单时不送模型，IME组合不拦截。
刷新会话补client与最新请求代次隔离，切会话重建菜单。版本0.1.2，协议/Python版本不混同。

**测试/本地构建**：394项前端、7项交付、5项dashboard合同通过；tsc通过。
`/tmp/vorto-info-tests.log`、`/tmp/vorto-info-delivery.log`、`/tmp/vorto-info-backend.log`、
`/tmp/vorto-info-tsc.log`。系统Python/.venv无pytest失败保留，使用既有miniforge环境完成。
`/tmp/vorto-info-bundle.log`签名bundle成功；构建保留已有大chunk及未公证警告。
最终buildId `20261009T062944690Z-d0986f69`，sourceFingerprint
`d0986f69e152bff14f5a34f2521f5fbd442478b95803e05c1d24a36f10f776dc`。

**真实浏览器组件操作**：ego-browser Space114已结束，原组件生产夹具验证加号菜单开关、
`/info`键盘选择并清草稿、General不列源码引用、未知命令不发送、Shift+Enter换行、普通
Enter发送、effort禁用。`/tmp/vorto-info-browser-evidence.json`通过；是合成快照隔离组件，
不是已安装原生窗口。首次菜单被原composer overflow:hidden裁切，修复后通过。
864px浏览器低于Desktop原900px最小宽度产生的页面溢出保留诊断，夹具单独覆盖最小宽度，
不宣称改变Desktop最小窗口合同。截图Page.captureScreenshot超时；截图视觉验收未通过/未完成。

**独立运行包**：`/tmp/vorto-info-package-evidence.json`/probe.log通过：签名bundle内runtime
在独立临时目录、随机loopback端口运行，用合成模型完成一个原plan回合，再读真实sessions。
used_tokens=2987、历史预算8000、模型窗口unknown、compact_enabled=true、will_compact=false；
请求未带reasoning_effort。只终止探测自身进程。此项不证明真实oMLX effort、压缩执行成功或
provider硬预算。本轮不调用真实provider，不重置其配置，不启动任何原生GUI。

**安装**：沿用既有授权，先验证旧包备份，再事务替换/核验到`/Applications/VortoCode.app`；
日志`/tmp/vorto-info-install.log`，备份
`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-NuqaXZ/VortoCode.app`。
安装后签名/sidecar烟测结果追加于下方；没有退出或重启正在运行的Desktop。

**未验收/发布**：真实oMLX本轮能力、原生GUI/焦点/IME/Spaces/VoiceOver/Hook和截图视觉验收
未验收；既有失败记录保留。无公证/正式发布/提交/合并/新增自动推进或provider权限。

安装最终核验：sidecar smoke 549ms通过，SDK构造不调用真实模型；
`/tmp/vorto-info-signature.log`签名核验通过，identifier=com.vorto.vortocode，Team=8UTMY2WR9H。
`/tmp/vorto-info-installed-evidence.json`实际Info.plist版本0.1.2，注册入口仅
`/Applications/VortoCode.app`；只撤销本次构建副本注册，不删除文件或重置其它项目注册。
`git diff --check`通过。浏览器截图超时仍未验收，未启动/退出原生窗口，运行中的旧窗口不会
因安装自动加载新版本；用户自行⌘Q后重开才加载v0.1.2。

## 58. macOS关闭隐藏与显式恢复（2026-10-09）

**源码**：当前关闭路径从minimize改hide，原prevent_close/runtime生命周期保留；应用保留
常规Dock入口，Reopen/单实例显式show/unminimize/focus，⌘Q原Exit清理不变。
内存MainWindowPresentation标记用户隐藏意图；原Ready/PageLoad/4秒兜底调度到UI线程并
重核该意图，避免迟到启动事件复活窗口。hide失败清除标记并报告，防止销毁会话。
其它平台CloseRequested仍不介入，不新增数据库、任务池、provider权限或自动推进。

**测试/构建**：`/tmp/vorto-hide-rust.log`81项既有Rust合同通过，包含回环端口检查；
这些合同不模拟macOS窗口隐藏/恢复，不能当作原生验收。git diff --check通过。
版本配置/锁文件一致0.1.3，签名构建/安装/烟测在下方追加最终结果。

**原生与发布边界**：遵照用户暂缓原生验证要求，没有启动、退出、绑定或操作任何原生窗口，
没有聚焦/输入法/Spaces/VoiceOver/Hook验收。隐藏后Dock呈现、恢复焦点、hide失败实际表现、
启动时关闭、后台任务继续运行及正常Quit清理仍未验收，旧失败不改记通过。
本輪不调用真实模型，不修改oMLX服务/设置；正式发布和公证未执行。

最终交付：7项交付合同通过（`/tmp/vorto-hide-delivery.log`），签名包构建成功
（`/tmp/vorto-hide-bundle.log`）。buildId=`20261009T063603708Z-377d2837`，sourceFingerprint=
`377d28374394da56b77d854cd4bc239458ad3afd2da94f11eb0e2e981c770ba4`。
备份核验后安装到`/Applications/VortoCode.app`，旧包保留在
`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-9TaQVr/VortoCode.app`。
`/tmp/vorto-hide-install.log`安装及包内runtime smoke 551ms通过；SDK构造/健康检查无推理。
签名核验日志`/tmp/vorto-hide-signature.log`；单入口注册凭据`/tmp/vorto-hide-installed-evidence.json`。
签名/烟测不替代关闭与Dock原生验收，未正式发布。用户自行⌘Q退出并重开才加载新窗口行为。

## 59. 上下文百分比浮层（2026-10-09）

**源码**：用户截图中的28×28通用按钮规则挤压已定位；ContextStatus独立尺寸/nowrap/
flex-shrink修复，模型标签最大宽度与footer换行。按钮仅百分比，点击body portal小浮层，
不触发Inspector打开；信息页大段上下文收起。Esc/外部/关闭与焦点恢复、视口限制、滚动/
resize定位和会话切换清理实现；原快照/未知/预算区分保留。无provider/台账/执行流程改动。

**测试/构建**：394项前端、7项交付合同、tsc通过（`/tmp/vorto-context-tests.log`、
`/tmp/vorto-context-delivery.log`、`/tmp/vorto-context-tsc.log`）。覆盖触发按钮仅百分比、
无快照不为0、窗口与历史预算区分、压缩未知/关闭/待尝试，右侧不常驻token长文。
签名构建`/tmp/vorto-context-bundle.log`通过，未公证；git diff --check通过。
0.1.4 buildId=`20261009T064723549Z-826495d9`，sourceFingerprint=
`826495d94a5bbb7dea5304112e043f8f653deb24e089c08ae41c32932645acd7`。

**真实浏览器隔离组件**：ego-browser Space115原生产组件+原完整composer-footer结构，
合成窗口数据0.6%，按钮量测47.9×30、不溢出。点击小浮层未打开侧栏，焦点进入浮层，Esc
返回入口，关闭按钮/实际外部点击收起。320px输入框下百分比和发送按钮均在框内，composer
无横向溢出，280px浮层在视口内；`/tmp/vorto-context-browser-evidence.json`通过，空间已关闭。
首次「外部」选择的textarea中心被浮层覆盖，工具正确拦截，改用真实外部标题验证；保留记录。
Page.captureScreenshot再次CDP超时，截图视觉验收未完成，未反复尝试或改为原生控制。

**安装/隔离运行**：已有授权下备份、事务替换`/Applications/VortoCode.app`到0.1.4；
`/tmp/vorto-context-install.log`包内runtime健康/SDK构造smoke 554ms通过，无推理。
签名核验`/tmp/vorto-context-signature.log`，单入口/版本凭据`/tmp/vorto-context-installed-evidence.json`。
未启动、关闭或重启当前原生窗口；旧窗口自行⌘Q后重开才加载新版本。

**未验收/正式发布**：本轮真实provider未调用，原生浮层/焦点/IME/Spaces/VoiceOver/Hook、
截图视觉验收与正式发布未验收；历史原生失败不改记通过，不启用自动检查/推进或新权限。

最终签名核验通过，版本0.1.4，注册仅/Applications入口；备份保留于
`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-R5ZO3h/VortoCode.app`。
仅撤销本次构建副本注册，不删除无关应用或重置全局注册数据库。

## 60. 日常页面内容收敛（2026-10-09）

**源码与内容核对**：原App首页/导航、SessionInfo、SettingsModal、AccountPane、PluginsPage。
运行状态优先、空区块隐藏、详情折叠；确认/断连有原入口，失败活动保留。未配置模型直接
进入模型设置，本地模型无需登录；新对话/任务概念分开，重复工作台入口合并；版本折叠、
插件说明缩短。新增DESKTOP_CONTENT_GUIDELINES规范，原任务/权限/生命周期边界不变。
首页主App只做源码核对，本轮未运行完整原生App流程。

**视觉审查阻塞**：Product Design审查流程preflight无保存上下文；iab返回Browser is not
available。备用ego-browser的Page.captureScreenshot仍CDP超时，未转原生应用控制或反复尝试。
按audit/SKILL.md「Do not claim an audit if the actual flow could not be accessed and captured.」
完整截图审查未完成；以下DOM/操作验证不当作视觉验收。既有截图和原生失败记录保留。

**源码测试与构建**：`/tmp/vorto-content-tests.log`396项前端、`/tmp/vorto-content-delivery.log`
7项交付、tsc通过；新增合同核对空闲简洁、等待确认、失败信息优先与断连处理入口。
`/tmp/vorto-content-bundle.log`为签名bundle记录，已有大chunk/未公证警告保留。
buildId=`20261009T070410058Z-6f92ea69`，sourceFingerprint=
`6f92ea6948993390de9c7f1618130bc6983e8c73311d3dba9507fca0c18b1b75`。

**浏览器组件操作**：原生产组件隔离夹具、合成数据，ego-browser Space116；证据
`/tmp/vorto-content-browser-evidence.json`。空闲不展示空步骤/活动，详情默认折叠并能打开；
执行显示实际失败活动、确认指向原卡、断连打开连接设置。设置/插件的后续操作追加最终结果。
不保存真实配置、登录账号、发送模型请求或替换真实provider；首页入口未以浏览器覆盖整个App。

**交付边界**：版本0.1.5，签名安装/原runtime smoke和单入口证据在下方追加。
原生UI/焦点/IME/Spaces/VoiceOver/Hook、截图视觉审查及正式发布未验收；无公证/提交/合并，
done/验收/交接处理/持久交付/Goal仍独立，不启用自动推进或新provider权限。

浏览器最终操作补充：常规页details.open=false，点击「版本与构建信息」可展开；账号页
明确本地/第三方无需登录且链接进入原模型设置。插件页选择项目和浏览器设置按钮均到原
回调，夹具只记录意图。versionHidden的getClientRects测量为false，未将它用于视觉断言；
保留该测量与截图缺失边界。Space116已结束，仅组件交互通过，不声明完整视觉审查通过。

最终交付：签名bundle成功、旧包备份核验后安装0.1.5，运行包smoke 546ms通过，
`/tmp/vorto-content-install.log`记录备份
`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-mZiJxa/VortoCode.app`。
`/tmp/vorto-content-signature.log`签名核验通过，`/tmp/vorto-content-installed-evidence.json`
记录实际版本0.1.5、注册仅/Applications/VortoCode.app；只撤销本次构建副本注册，
不删除其它应用或重置全局库。git diff --check通过。未启动或退出旧原生窗口，
用户自行⌘Q重开后加载新版；完整视觉审查与原生验收保持未完成。

## 第61节：Desktop 按授权读取本机文件（2026-10-09）

**源码与合同**：0.1.6沿用files领域、build_session、原gate/trust/deny/审计；
Desktop可读取完整路径的单个普通UTF-8文本，固定64KiB，不改项目读写围栏。
审批版本检查、敏感原/规范路径、目录/管道/二进制拒绝、取消传播、串行确认、
General profile与HTTP边界及客户端迟到请求隔离已实现。一次批准不保存目录授权。
合同见DESKTOP_FILE_ACCESS.md、PRD第61节；任务生命周期/provider自动化边界保持。

**测试**：121项合同测试通过（authorized_local_files/general_workspace_session/workspace_scope/
trust_setting/three_end_contract/repo_files_scope/trust_levels）；39项协议/MCP/General回归通过，
两组存在重叠，不把合计称为独立用例数。前端397项、交付7项通过，tsc --noEmit与
独立QA构建通过。源码git diff --check通过。Starlette/httpx弃用警告保留，不影响通过。

**浏览器**：ego-browser Space117使用原SettingsModal/trustCard及原useTrustLevel夹具。
General只读选项可切换，完全信任禁用，明确项目外文本与跨目录写/执行不包含；
A工作区待保存切到B后，旧GET/PUT返回仍显示B/ask/非busy，未发旧成功横幅。
`/tmp/vorto-file-browser-evidence.json`保存交互证据；夹具不保存真实配置。
Space117结束，自己的18789静态QA服务停止。仅组件/请求隔离验证，不宣称完整App或视觉审核。

**独立运行包**：`desktop/qa/local-file-package-probe.py`启动本轮签名包Gateway、临时General
目录、独立合成模型与工作区外测试文件。真实WS原agent_confirm/response路径：拒绝不返回
内容，允许一次读取且档位仍ask，PUT reads后新回合0次确认读取；原审计有读取记录，
General ceiling仍reads，模型请求无reasoning_effort。全部通过。自己创建的runtime/model
探针结束；未访问真实模型凭据或其它会话。证据`/tmp/vorto-file-package-evidence.json`、
日志`/tmp/vorto-file-package-probe.log`，合成模型不能代表oMLX实际模型遵守新工具合同。

**本地构建/安装**：Developer ID签名包0.1.6构建成功；buildId
`20261009T072417143Z-f4415e10`，sourceFingerprint
`f4415e1055de5ab871515f6e2cf57343c64b11de614d75f938b2533fe7dc68a9`。
原事务安装先核验备份与候选再替换/核验，安装到`/Applications/VortoCode.app`，Plist版本0.1.6；
备份`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-xPEETZ/VortoCode.app`。
已安装runtime smoke 549ms通过（SDK/健康，无推理），签名核验通过。日志
`/tmp/vorto-file-bundle.log`、`/tmp/vorto-file-install.log`、`/tmp/vorto-file-signature.log`。

**未验收/未发布**：真实oMLX的新工具选择/审批用户流、原生GUI/焦点/IME/Spaces/VoiceOver/
Hook未验收，旧失败记录保留。未启动/退出现有窗口，用户自行⌘Q重开后加载新代码。
未公证、提交、合并或正式发布；不存在跨目录写/执行、敏感批准、目录grant或自动推进。
下一步可独立完善按目录/期限/撤销的只读grant合同，之后再单独设计跨目录写入，不用提高
档位隐式开放全文件系统。

## 第62节：确认卡升档与监管（2026-10-09）

**源码**：确认卡复用原trust设置/请求隔离，可保存更高且不超过上限的档位，不代答本次审批；
保存失败保留档位，断连/保存中禁用。Desktop监督由可信原装配实施：effective最高reads，
历史FULL不删除但降档，auto_approve不越过；原文件工具已识别敏感参数硬拒绝，shell含只读
仍EXECUTE；原危险规则/deny/沙箱保持。CLI/普通Web合同不改，不启用新provider/自动模型检查。

**测试/本地构建**：118项相关Python合同通过，覆盖旧FULL/auto_approve不能免写/执行/外发、
无人值守拒绝、敏感文件read/write硬拒绝及内容保持、危险命令批准不可绕过、只读shell仍需
确认及原三端门。前端400项、交付7项通过，tsc与独立QA构建通过，git diff --check通过。
原Starlette/httpx弃用警告仍在。第一次构建期间补充监管代码，原构建摘要拒绝该候选，未安装；
冻结源码后重建成功，正确保留构建与源码一致性门，不把旧候选当新实现验收。

**浏览器**：ego-browser Space118使用原ConfirmationTrust/useTrustLevel夹具。保存失败
显示测试错误且ask/未批准；再次保存成功为reads，确认仍未批准；点击允许一次才批准。
最高档不再显示升档按钮，卡说明写/执行/外发仍审批。证据
`/tmp/vorto-supervision-browser-evidence.json`，不保存真实档位；Space118已结束、
自己的18789 QA服务停止。组件交互而非完整原生流程/视觉审核。

**独立运行包**：`desktop/qa/supervision-package-probe.py`使用独立Project运行包Gateway、
合成模型和临时文件。原WS/HTTP链路5条用例通过：拒绝读取、一次允许、预授权读，旧FULL
实际reads下拒绝写文件仍不存在，逐次人工允许才创建。审计存在，profile仍local、levels
仅ask/reads、supervised=true；模型未发送effort。证据
`/tmp/vorto-supervision-package-evidence.json`，日志`/tmp/vorto-supervision-package.log`。
自己创建的模型/runtime结束。未调用真实oMLX，不将合成结果当作真实模型验收。

**签名构建**：0.1.7，buildId `20261009T074018992Z-01443ae9`，sourceFingerprint
`01443ae9e2640de3bab8ae3896658eca2a07ca945e363cf43684f6ecb9a74074`，Developer ID签名成功，
日志`/tmp/vorto-supervision-bundle.log`。安装与已安装包核验追加在下方。

**剩余边界**：不是识别所有危险的证明；任意脚本、第三方工具隐含副作用、普通文本中的
未被路径识别秘密不由此有限规则全面分类；原被批准的隔离任务内部仍按原范围执行。
新监管的真实oMLX用户流程/原生GUI/IME/Spaces/VoiceOver/Hook未验收，旧失败保留。
未公证、正式发布、提交或合并，未启动/退出当前窗口；done/验收/交接/交付/Goal分离。

最终安装：旧包备份核验位置
`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-mkc2ZR/VortoCode.app`。
事务安装至/Applications/VortoCode.app，Plist版本0.1.7，已安装sidecar smoke 552ms通过
（SDK/健康无推理），签名核验通过，构建摘要与最终源码一致。日志
`/tmp/vorto-supervision-install.log`、`/tmp/vorto-supervision-signature.log`。
只取消本轮构建副本注册并刷新主目录入口，不删除应用或重置库。未重启当前窗口，
用户自行⌘Q重开加载新版；真实模型、原生与正式发布边界保持未完成。

## 第63节：空白会话记录收敛（2026-10-09）

**源码**：原session_store内容判定复用到Dashboard；新纯空save返回True表示无需写盘，
不创建对话JSON，不删除既有文件。Dashboard只隐藏无内容/无运行/确认/任务的条目，
计划/历史/活动/队列/显式名称仍保存显示。Desktop空白页复用sid，新建连接互斥防重复点击。
原生命周期/TaskLedger/监管/provider边界不变，没有批量删历史或另建会话表。

**测试与本地构建**：第一组63项存储/WS/容量/Dashboard/actor通过，后续会话/事件/恢复73项
通过（有重叠，不累加为独立用例）。验证空live首次消息显现、旧空JSON隐藏但原文不删、
显式名称和队列保留、后台任务零消息仍显示、纯空保存no-op、容量与活动保护不退化。
原context_snapshot测试改为真实用户消息后检查上下文持久化，新增无内容测试明确新合同。
前端400项、交付7项通过；tsc与git diff --check通过，Starlette/httpx弃用警告保留。

**剩余边界**：原生Desktop点击/焦点/IME/Spaces/VoiceOver/Hook未验收，不启动/退出窗口。
本轮浏览器完整App点击未独立验收；独立运行包WS/REST与存储结果追加下方，不能推广为
原生或真实oMLX模型验收。未正式发布、公证、提交或合并；旧失败与历史资料保留。

独立运行包结果：`desktop/qa/draft-package-probe.py`用本轮签名General Gateway真实WS/REST
连续连接55个新sid，覆盖50个会话容量的淘汰：recent_before_send=0、empty_files=0。
随后合成模型3个用户回合，最近列表恰好含file-deny/file-once/file-preapproved，拒绝审批
也保留已发送消息；旧只读审批/审计合同继续通过。证据`/tmp/vorto-draft-package-evidence.json`，
日志`/tmp/vorto-draft-package.log`。临时目录
`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vorto-file-package-p4fq1z_n`，
仅自己的runtime/model已结束，不访问真实provider或其它会话。

签名构建0.1.8成功，buildId `20261009T075711091Z-9993a681`，sourceFingerprint
`9993a68133d23b5551fe69b8674e80065763170baaba632f0b26b5ad93d0a288`；日志
`/tmp/vorto-draft-bundle.log`。安装结果追加下方。运行包协议客户端通过不等同Desktop原生
点击/浏览器完整App验收；真实oMLX未验收，正式发布未完成。

最终安装：旧包备份核验在
`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-QqQ0fx/VortoCode.app`。
事务安装/核验/回退合同保持，/Applications版本0.1.8，已安装sidecar smoke 549ms通过
（SDK/健康，不推理），签名核验通过；日志`/tmp/vorto-draft-install.log`、
`/tmp/vorto-draft-signature.log`。只撤销本轮构建副本注册并刷新主入口。
最终源码与构建摘要匹配，git diff --check通过，未重启当前窗口；用户⌘Q重开加载新版。

## 第64节：当前执行工作台与审批回执恢复（2026-10-09）

**源码**：工作台主导航收为当前执行/看板/更多，次级页原入口保留。发送时遵循原焦点规则，
已有其他页只提示不抢焦点。聊天计划缩为查看入口，当前执行显示原plan步骤/完成数、当前running
工具、owner_session精确匹配的原任务快照、执行者与轮次；开发任务定位原TaskCard再展开依赖图。
没有plan显示未提供，不制造百分比/拓扑。父任务不作为依赖断言；done与验收/交接/Goal分别记录。
新增WorkbenchNav与ConfirmationReply从原App拆出，业务沿用原clientRef/TaskFeed/确认Future。

审批提交成功仅awaiting，失败保留卡并锁定重复答复；closed或手动核对不再等待才收起。
check只读、所属会话隔离；id/nonce/client/sid守卫迟到响应，8秒无回执保留未知。核对仍pending
明确回到人工答复，不自动重发。待处理列表去所属会话原确认卡，不从第二入口盲目批准。

**测试**：Python协议冻结/WS会话/三端/本机文件授权/授权档位131通过（原Starlette弃用警告1）；
Desktop Vitest 412通过，TypeScript通过，交付测试7通过。覆盖提交未闭卡、双击去重、失败
保留、显式重试、receipt先到、换client/sid/id迟到隔离、计划缺失和生命周期独立，以及只读check
owner/跨会话/已答复/未知ID不解开Future。git diff --check通过。
日志：`/tmp/vorto-workbench-{frontend,python,delivery}.log`。

**真实浏览器、隔离组件**：Ego Space119在回环静态生产构建中操作原WorkbenchNav/SessionInfo/
ConfirmationReply与controller。计划1/3、进行中工具、reviewer第2轮、编排入口回调正常；无计划
提示正常。审批合成发送异常仍保留卡，DOM两答复按钮disabled；核对后人工拒绝发送次数1→2，
运行端关闭回执夹具收卡；更多→预览后菜单关闭，返回当前执行正常。完成后space与静态服务器
已关闭。夹具数据为合成快照，未连接真实模型/台账；编排入口只验定位回调，未进行完整App中的
真实开发DAG互动。无截图视觉审查证据，不等同原生Desktop布局验收。

**独立运行包**：`desktop/qa/workbench-package-probe.py`使用签名包Gateway、独立临时目录与合成
OpenAI兼容模型，经真实WS/REST得到agent_plan的in_progress/pending。拒绝/一次批准/reads预授权
三种本机文件链路通过；前两种check pending=true、不改变Future，其他会话check=false；答复后
closed和check=false正常，审计保留。不发送effort、不接真实oMLX、无GUI，子进程finally清理。
证据`/tmp/vorto-workbench-package-evidence.json`及日志`/tmp/vorto-workbench-package.log`。
首次探针误用固定工具调用ID，而原客户端重建ID，合成模型提前结束造成等待核对超时；修正为
依据实际tool_calls工具名后通过。失败证据`/tmp/vorto-workbench-package-first-failure.json`保留，
未把这次失败写为产品或原生通过。

**交付**：最终构建/安装身份及核验结果见下方追加记录。原生GUI/真实provider推理与真实开发
下派DAG仍未验收；此前原生失败/待验记录不改。未公证、未正式发布，未启用自动检查/推进。
下一阶段按第64节：会话输入草稿切换保护和明确人工触发的本地模型推理/工具诊断。

最终签名包0.1.9，buildId `20261009T082441538Z-8f14e5e3`，sourceFingerprint
`8f14e5e398bee1aad4ac30ae7a68539c670537060305115a985f243126892013`，最终源码摘要核对一致。最终独立运行包探针再次通过。
本地事务安装到`/Applications/VortoCode.app`，已核验备份`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-MwUZfT/VortoCode.app`；
安装后runtime smoke 549ms通过（SDK/健康无推理），签名核验通过。构建/安装/签名日志
`/tmp/vorto-workbench-{bundle,install,signature}.log`。仅取消仓库构建副本的LaunchServices注册
并刷新Applications主入口，没有全局重置、GUI启动/退出或其它项目进程清理。
当前正在运行的旧GUI未重启，用户需自行⌘Q后重开加载本轮版本；正式发布仍未进行。

## 第65节：纠正看板与会话混层（2026-10-09）

第64节导航经用户反馈不合适，原验证证据保留但该导航被本节取代。源码0.1.10：左侧任务中心
在主区域承载看板/任务/目标/待处理；管理路由统一由isManagementTab定义，原面板和客户端复用。
会话侧栏移除四类管理入口、下派列表/编排图及全局计数，顶部固定当前执行；保留本轮plan/tool/
确认/队列及折叠会话详情，辅助项目工具仍在更多。管理页后台变化仅提示，不抢主区域焦点。
看板原工作区切换保持任务中心；打开会话转对话/确认卡，失败不伪装成功导航。

前端414项通过，TypeScript通过，交付7项通过；测试包括管理/会话路由分类、导航无管理泄漏、
SessionInfo无下派/Goal/图，原审批故障恢复测试继续通过。无后端合同改动，Python验收沿用第64节，
本轮没有再次声称新的后端测试结果。日志`/tmp/vorto-management-{frontend,delivery}.log`。

真实浏览器Ego Space120操作原ManagementNav/WorkbenchNav/SessionInfo的生产构建隔离夹具：
管理导航为看板/任务/目标/待处理，会话导航无上述按钮；当前执行步骤1/3、工具显示、无下派列表、
无计划提示通过。Space和仅本轮静态服务器已关闭。此证据是原组件与合成数据，非完整App的
跨runtime导航或原生布局验收，未获取视觉截图。审批/provider/自动推进无新增授权。

首次构建期间最终入口检查补去旧默认跳转/全局计数，摘要保护拒绝该候选，未安装。
记录`/tmp/vorto-management-build-source-drift.log`保留；最终冻结源码重建，最终身份追加如下。
原生GUI、真实provider与完整跨会话管理点击仍未验收；以前失败记录不改，正式发布未进行。

最终构建0.1.10，buildId `20261009T083754863Z-9501b883`，sourceFingerprint
`9501b8830a8752ce37a2e213507a8d1cf0d2fa2064fd52fb267b780ba5f983b0`，摘要与最终源码一致，签名通过。
独立签名运行包以原第64节探针复测三类文件读/计划事件/确认前后只读核对及跨会话隔离通过，
证据`/tmp/vorto-management-package-evidence.json`；不证明新的跨runtime页面导航/原生UI。
已备份`/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-uEZpYo/VortoCode.app`，事务安装到`/Applications/VortoCode.app`；
安装后runtime smoke 551ms（SDK/健康无推理）及签名核验通过。日志
`/tmp/vorto-management-{bundle,install,signature,package}.log`。仅注销仓库构建副本的注册并
刷新Applications主入口，无GUI启动/退出，无其它项目进程清理，无正式发布。
下一阶段需专门复核看板跨会话panel定位：旧代码的client守卫在主动切sid重建client时
可能中止定位；本轮未把完整App的这一路径验为通过，需独立复现/合同测试。

## 66. 执行过程与最终输出分层（2026-10-10，源码 0.1.11）

用户确认朴素风格、无对话头像、工作区域选择在输入框上方，并授权继续实施：运行时
展示过程，最终输出后缩略；当前计划/任务步骤在侧栏。修改复用原 TurnTimeline、
SessionInfo、ProjectPicker 与 App 原消息/rid，不新增领域库或执行池。

自动折叠必须是相同 rid 有非空 assistant 结果且该回合非 active。只有工具完成或 busy
停止不能证明已交付；无结果、仍有 running、failed/blocked/cancelled/timed_out 保持展开。
收起后可人工展开原记录；手动收起异常仍在摘要保留“需要关注”。执行耗时仍是原 phase
累计时长，非新增墙钟或费用计量。模型思考只使用原公开活动标签，不生成内部推理。

发送消息不再自动开 info；已有打开状态保持。SessionInfo 投影原结构化计划与异常，
不重复普通工具过程；无计划不创建计划区。工作区域选择器移到输入框上方，已有会话
也可见；运行、确认、切换、启动期间禁用，仍走原切换入口。对话原本使用文字署名，
没有聊天头像，未新增图像头像；完整浅色视觉重绘尚未实现，主题设置保持。

前端合同包含停止无结果、有结果后收起、运行与异常不得折叠；真实浏览器 Ego Space122
操作原组件合成夹具，最终输出 open=false/list=false，人工展开工具历史成功，新回合
open=true，无输出停止 open=true，失败 open=true/需要关注=true。截图
`/tmp/vorto-process-browser.png`、`/tmp/vorto-process-output-browser.png`。此为组件交互证据，
不是完整 App/真实 provider 或原生界面验收；不以截图宣称与设计稿一致。独立回环端口
43987、浏览器验证页已关闭；4318 已被占用，未处理该进程。

设计稿 `exec-e5bb6e77-65bd-4b9e-830f-0217f1353d76.png` 已生成并在 Preview 打开。
设计图中模型与工具结论为示例，不作为实际模型连接证据。本轮未替换已安装 0.1.10，
未进行签名运行包、真实 provider、原生 GUI 或正式发布。旧原生失败/未验收记录保留。
最终前端测试、构建、交付脚本测试日志分别为 `/tmp/vorto-process-{tests,build,delivery}.log`。

## 67. 日常页面整体布局（2026-10-10，0.1.12）

承接第66节，用户授权继续朴素风格。新增 `desktop/src/daily.css`，App 在原 App.css 后
加载，保留原主题偏好；新对话与任务中心是主要导航，插件/产物入口在更多中。新对话
取消装饰标识和渐变，标题“开始一项工作”；正文15px，标题22px，模型/权限13px，
输入框取消重阴影；原组件与权限/任务领域状态不改。任务步骤是当前会话入口名称。
1024px 窗口侧栏为覆盖层，保留关闭入口；管理页仍在主区域，不进入侧栏。

前端416项、交付脚本7项通过。构建通过，保留原 Monaco 大块警告，没有声称包体优化。
浏览器 Ego Space123 验证原组件/产品CSS的布局夹具 `qa/daily.html`：空白/执行/结果、
浅/深色、侧栏关闭/打开、上下文弹层、工作区域选择/退出、输入合成任务通过。
1024x768 下 scrollWidth=1024，composer宽756，侧栏 position=absolute；上下文与工作
区域菜单边界在视口内。原CSS固定Markdown标题色和11px高优先级模型样式复核后已改。
Runtime/Log开启后的观察轮次无 exceptionThrown/entryAdded；不声称扫描所有历史日志。
截图 `/tmp/vorto-daily-{empty,working,final,dark,narrow}.png` 已查看，前三张已在Preview打开。

夹具不是完整App、真实客户端会话恢复或原生窗口。完整设计一致性报告 `design-qa.md`
保持 blocked；并非组件检查失败，而是完整App视觉证据缺失。原生已明确暂缓，不等待
解锁、不启动GUI。第65节跨会话定位及第66节真实rid/恢复折叠仍独立待验收。
仅关闭Space123及本轮回环43987服务器，没有处理其它端口、桌面端或协调会话。

本地签名0.1.12构建：buildId `20261010T075127306Z-beea833a`，sourceFingerprint
`beea833aa2a222d1dfb35dd8f9ecb87e87de3aea12f4237256205b4ebc5d3677` 与冻结源码核对一致。
独立运行包复用 workbench-package-probe：合成provider、本地临时工作区、文件拒绝/
单次批准/授权档位、计划事件、确认前后只读核对和跨会话隔离通过。
证据 `/tmp/vorto-daily-package-evidence.json`，real_provider=false；这不是GUI布局验收。

已核验备份 `/var/folders/zm/skbnydt12gq5023jfjb2wf_00000gn/T/vortocode-desktop-backup-MH6mZr/VortoCode.app`
并事务安装 `/Applications/VortoCode.app`；安装后runtime smoke550ms，SDK/健康无推理。
本轮没有退出/启动原生应用，旧进程是否加载新文件未当作验收，用户自行退出重开。
签名、公证、真实provider、原生GUI、正式发布分别报告：签名按最终日志核验，未公证、
未调用真实provider、原生暂缓、无正式发布。既有失败断言与历史未验收记录保留。
日志 `/tmp/vorto-daily-{tests,delivery,build,bundle,package,install,signature}.log`。

第67节最终核验：安装签名核验通过，bundle identifier/Developer ID指定要求一致；最终源码
摘要仍匹配0.1.12构建。相关diff无空白错误。真实provider、原生GUI、公证/正式发布未验收。
