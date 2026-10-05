# Agent 与任务协作机制

> 当前实现：一个会话主 Agent + 按需委派的角色子 Agent + 隔离开发工具。
> 早期固定五角色批处理流水线已经退役；本文描述当前运行路径。

## 模块边界

| 模块 | 职责 | 不承担的职责 |
| --- | --- | --- |
| `src/llm/hard_budget.py` | 严格适配器门、请求前预留、累计 token/请求/工具/时间预算 | 默认 provider 能力推断、持久认领和授权 |
| `src/llm/streaming.py` | 执行中断信号、受检查回调与本地流清理 | 任务台账、失效判定、远端费用停止 |
| `src/agents/bounded_check.py` | 单次只读检查装配与结构化预算结果 | 自动调度、回执或任务验收 |
| `src/agents/agent_loop.py` | 模型回合、工具调度、上下文和权限门 | 客户端导航、任务持久化 |
| `src/agents/main_agent.py` | 标准主 Agent 工具装配、旧导入兼容 | 开发流水线和角色执行实现 |
| `src/agents/dev_policy.py` | 环境预检、重试策略、测试改动与诚实结果摘要 | 模型执行、客户端通知 |
| `src/agents/dev_tools.py` | 隔离实现、并行/依赖接力、集成验证、审查与 PR 门 | 会话和角色注册 |
| `src/agents/subagents.py` | 角色定义解析与注册表 | 模型实例创建 |
| `src/agents/delegation.py` | 角色工具装配、委派、结果回传和后续轮次 | 自建任务数据库、客户端协议 |
| `src/gateway/session_actor.py` | 前台/检查共用唯一槽位与清理、输入 FIFO/优先项、停止与恢复 | WebSocket、模型、存储格式和事件协议 |
| `src/gateway/session_event_stream.py` | 事件序号、原子补收/订阅、有界回放和广播 | 工作区选择、WebSocket、模型执行 |
| `src/gateway/session_events.py` | 分段事件 journal、容量限制与磁盘恢复 | 实时订阅和模型执行 |
| `src/gateway/tasks.py` | 精确任务身份校验、共享台账、后台执行与崩溃恢复 | 模型和角色选择 |
| `src/gateway/task_recovery.py` | 开发恢复身份、来源/计划版本校验、单次恢复与血缘投影；复用原执行池 | HTTP、模型、第二套台账或调度器 |
| `src/gateway/collaboration.py` | 委派状态、任务消息、轮次校验与发起 Agent 验收 | LLM、WebSocket、自动启动会话 |
| `src/gateway/task_questions.py` | 台账内的问题合同、期限、答复幂等与轮次变更校验 | 存储实例、模型、调度器 |
| `src/gateway/dev_questions.py` | 开发问题绑定任务/块/计划版本，清理后答复恢复与固定轮次合同 | 模型、Git 清理、第二套任务池 |
| `src/agents/task_questions.py` | 绑定当前任务的提问工具与持久暂停信号 | 接受任意任务身份、回答或扩大权限 |
| `src/gateway/handoffs.py` | 后台完成收件箱、结果版本与持久处理回执 | 模型调度、任务成功判定 |
| `src/gateway/continuations.py` | 结果版本授权、一次认领、撤销和启动中断对账 | 模型调度、汇报送达、任务验收 |
| `src/gateway/continuation_checks.py` | 注入式 actor 检查桥、预算记录、持久汇报回执后确认交接 | provider 选择、独立调度池、任务验收 |
| `src/gateway/check_reports.py` | 校验台账结果、原会话持久汇报、尝试身份去重 | 模型调用、扩大授权、用户已读判定 |
| `src/gateway/sessions.py` | 安全容量淘汰、会话生命周期、真实保存结果、先保存再追加展示消息 | 结果验收、自动启动检查 |
| `src/gateway/dispatch.py` | 请求幂等、下派、查询与有界返工；复用后台并发池 | HTTP、LLM、角色权限装配 |
| `src/gateway/task_dependencies.py` | 有界依赖合同、结果版本快照、固定失效记录与只读核对 | 调度池、模型、自动推进 |
| `src/gateway/task_dependents.py` | 有界反向发现、事件/启动/用户回合核对与原停止路径协调 | 持久索引、文件监听、轮询、自动推进或模型 |
| `src/gateway/task_scan.py` | 原 TaskLedger 的有界清单、覆盖检查点/归档、显式观察、历史文件回验及本地保存凭据 | 任务副本、监听/轮询、调度、长期全局依赖索引或模型 |
| `src/gateway/task_chain_budget.py` | 根任务中的链级准入与逐轮执行额度预留、只读用量投影 | token/费用计量、退款、调度池或模型 |
| `src/agents/dispatch.py` | 只读服务角色装配与执行预算限制 | 路由、第二套调度器 |
| `src/web/task_dispatch.py` | HTTP/主 Agent 共用服务装配、任务事件和当前用户回合提示 | 独立并发池、自动模型回合 |
| `src/web/routers/task_inbox.py` | 完成收件箱的类型化 HTTP 读取与手动回执适配 | 模型调用、验收、重新发布完成事件 |
| `desktop/src/connection/sessionConnection.ts` | 握手、断开代次与事件来源校验；复用唯一 clientRef | 项目注册、通知、面板数据与运行时启动 |
| `desktop/src/tasks/TasksPanel.tsx` | 任务入口、工作区/计划列表、任务卡与任务定位 | 请求、权限判定和业务状态推导 |
| `desktop/src/components/TaskCard.tsx` | 任务结果、任务消息、验收/返工入口 | 权限判定和任务状态推导 |
| `desktop/src/hooks/useTasks.ts` / `desktop/src/tasks/taskFeed.ts` | 任务/工作区快照、选中任务、请求与实时事件合并、暂停/恢复/取消 | 导航、系统通知、服务端状态推导 |
| `desktop/src/hooks/useTaskDispatch.ts` | 下派表单状态、请求重试身份、作用域与响应校验 | 后端执行和任务验收判定 |
| `desktop/src/components/TaskDispatchForm.tsx` / `DispatchTaskActions.tsx` | 研究/开发入口、服务任务直接交互 | 自建调度、静默授权 |
| `desktop/src/components/TaskHandoffActions.tsx` | 精确交付的手动处理说明、重试与迟到响应隔离 | 任务成功/验收推导、模型调度 |
| `desktop/src/components/TaskQuestionActions.tsx` / `hooks/useTaskAction.ts` | 问题与回答表单；问答/回执共用请求隔离 | 任务状态推导、自动回答、模型调度 |

依赖由装配层指向实现模块。开发和委派通过显式工厂注入创建 Agent，不能反向导入
`main_agent` 形成循环依赖。客户端只消费服务端权威状态，不通过对话文案猜任务成功。
后台收件箱直接依赖 TaskLedger 和静态/动态会话身份，不依赖委派服务的私有方法。
后续产品范围、阶段与验收条件见 [任务协作与架构收敛 PRD](./TASK_COLLABORATION_PRD.md)。
新增 [任务下派服务入口](./TASK_DISPATCH_API.md) 暴露 `/api/delegations`；模型工具和 HTTP
共用角色执行器与协作合同。HTTP 提交可在共享后台池独立执行，默认只读并限制步骤与超时。

## 主 Agent 与角色子 Agent

主 Agent 负责理解用户目标、按需委派、检查结果和向用户汇总交付。角色定义位于
`.vortocode/agents/*.md`；角色必须守住自己的职责，例如审核角色不修改代码。

- 默认研究子 Agent 只拿仓库只读工具。
- `tools: dev` 角色只增加 `dev_isolated` / `dev_parallel`，不拿直接写盘、Shell、PR
  或递归委派工具；每次委派及返工都经过现有确认门。
- 项目权限和会话 capability 边界继续生效；角色不能自授权限。
- 角色可选模型路由仍走现有 provider 配置。

## 委派任务合同

标准主 Agent 工具装配中的 `task` / `research_parallel` 已接入协作服务。任务复用
`.vortocode/tasks/<id>.json`，以 `kind=delegation` 区别于后台 `dev` 任务，不增加第二份任务台账。

任务记录包括发起 Agent 的 `owner_session`、目标，以及 `collaboration` 中的执行角色、
验收标准、执行轮次、验收状态、任务消息和累积污点。General 无目录会话继续不暴露这些仓库工具。
Web/Desktop 发起身份由当前会话提供；没有稳定会话身份的入口使用本次 Agent 实例身份。

执行状态与验收状态分开：

- 执行：`queued → running → done / failed / cancelled`；重启恢复残留 running 为 interrupted。
- 服务研究可由 `running → blocked` 等待回答；有效回答后使用下一轮 `queued → running`。
- 验收：`not_submitted → pending → accepted / rework_requested`。

`done` 只表示子 Agent 提交了非空结果。`accepted` 表示发起 Agent 记录了审查结论，
**不替代测试、Git 提交证据或 Goal 的 achieved 闸门**。模型的文字结论也不是运行验证证据。

## 完成后的交互

1. `task` 将合同先写入台账，再执行子 Agent；存储失败不得继续执行。
2. 子 Agent 结束后，结构化工具结果带 task ID、轮次、执行状态、验收状态、结果与错误，
   回到主 Agent 的同一回合。主 Agent 可以继续检查、调用工具或向用户解释阻塞。
3. `task_status` 读取自己发起的任务及消息，或列出最近任务。
4. `task_review` 用 task ID、精确轮次、accept/rework 和具体理由记录审查。
5. `task_followup` 向原角色补充要求或请求返工；从持久记录重建子 Agent 上下文，保留任务 ID、
   目标和验收标准，增加轮次。成功的新结果重新进入 pending，旧轮次结果与审查一律拒绝。
6. 已验收任务不能继续追加工作；新的工作创建新任务。每个任务最多执行三轮，超限交给用户决策。
7. Web/Desktop 主 Agent 使用 `task_status` 读取服务研究问题，再用 `task_answer` 提交明确答复。
   工具绑定当前会话，不接受 session 参数；通过注入的公共 DispatchService 回答操作保存并排队，
   不在前台内联重跑子 Agent。原角色、轮次、权限、步骤和超时限制保持生效。

任务消息记录 assigned、started、submitted、accept/rework、followup 和失败/取消事件，
每条具有稳定 ID、发送者、轮次和时间。重复接受不再产生额外消息；重复启动和重复提交被拒绝。
结果与消息有长度/数量上限并沿用审计凭据脱敏。子 Agent 或持久结果的污点必须传播给主 Agent，
不能因新回合、并行或恢复而获得更宽权限。

Desktop 任务卡显示执行角色、轮次、验收状态、交付结果和消息。验收或返工按钮只准备
发起会话中的输入草稿；用户编辑并发送后，由主 Agent 读取最新状态并执行。任务切换不会
直接放行写入、创建 PR 或启动新的模型回合。

## 当前边界与下一阶段

模型工具委派仍在主 Agent 回合内 await 子 Agent；HTTP 下派入口使用共享 TaskRunner 独立后台执行。
服务研究已支持持久提问、显式回答恢复和依赖任务的显式推进；后台完成仍不自动启动主 Agent。
独立后台 dev 与 HTTP 下派研究的 `task_handoff` 保留界面通知；主 Agent 通过 `task_inbox` 读取所属会话未处理的
终态结果，核对证据并汇报后用 `task_acknowledge` 写入处理回执。回执直接保存在原任务台账，
使用结果内容版本防止旧确认吞掉新结果；服务研究版本包括轮次、角色、验收标准和污点，
同文本的新轮次仍需重新处理。审查与消息时间变化不会凭空产生新交付；失败写盘不视为已处理。服务研究可由主 Agent 使用 task_status/task_review 检查和审查，返工通过原下派入口执行，
不借 task_followup 绕过共享并发池或原预算。处理回执不改变任务或 Goal 的
验收状态。结果按不可信工具内容处理，不借交接扩大权限。
HTTP `/api/task-inbox` 和 Desktop 手动处理入口复用同一收件箱，不需要模型回合。
回执保存后只广播任务更新，不把处理动作重新发布为完成事件；界面分别保留验收和处理状态。
Web/Desktop 的下一次用户回合会提示检查未读收件箱，不因后台完成自动启动付费模型回合。
HTTP 服务研究的前两轮可使用 ask_task_question；问题、调查进展和答复保存在原台账。
ToolTurnYield 在工具成功落盘后终止该模型回合，含提问的工具批次顺序执行，后续工具不再运行。
等待不保留挂起 coroutine 或并发槽。问题由原会话通过任务卡/API 回答，期限 24 小时，
回答消费原三轮预算中的下一轮；重复同一回答返回现状，不重跑。权限/角色在恢复前再次校验。
blocked 进入决策队列和运行时收件箱，不进入终态完成收件箱。取消关闭问题，过期问题拒绝恢复。
重启保留 blocked；答复已保存但未执行的 queued 按原规则恢复为 interrupted，不自动重放。
Web/Desktop 主 Agent 已接入 task_answer，回答依据只能是用户已明确提供的信息或核实事实；
未知偏好或授权仍须询问用户。下一次用户回合提示读取所属会话的有效问题，提示不含问题正文，
不因提问自动启动主 Agent。所属会话明确的后台 dev/dev-resume 已接入同一提问协议和主 Agent 答复；模型回合内委派暂停尚未接入。未注入共享下派服务的
CLI/IM/TUI 不提供直接回答工具。普通 followup 不接受 blocked 任务。
TUI 的委派工具已迁移到同一协作服务，终端只保留结果渲染、审计和 delegate 确认适配。

后续按以下顺序推进：

1. 会话 actor、事件层、Desktop 任务数据/面板与握手适配已提取；继续收敛 realtime 连接适配与跨域通知装配。
2. 完成交接、有界执行、持久认领/中断对账、actor 检查桥和原会话持久汇报已实现；支持只补发已完成检查而不重跑模型。严格 provider 适配尚未验证，生产自动检查保持关闭。下一步验证合格 provider、显式授权界面和完成事件装配。
3. 服务研究与后台开发问答已接入；开发问题固定任务/块/计划版本，清理后释放槽位，答复保留原预算。只读研究依赖任务已支持持久 waiting、前置验收、固定结果消费和显式失败传播；仍使用原 TaskRunner。
4. 只读研究任务链已支持累计执行额度、执行检查点、固定依赖失效与停止协调；有界反向发现、服务事件及启动/所属用户回合核对已接入。大台账稳定分页、持久游标、累计覆盖证据、显式保留/释放和有界外部变化观察已有本地实现。后续先设计历史长期迁移合同，再评估自动推进；token/费用硬上限需要独立合格 provider 合同。模型回合内委派暂停仍需独立合同。

## 验证

`tests/unit/test_task_collaboration.py` 覆盖真实主循环回灌与验收、持久返工上下文、所有权、
过期轮次、重复投递、模型失败、取消、存储失败、恢复、重试上限、污点与确认门。
`desktop/src/lib/taskCollaboration.test.ts` 覆盖草稿的所属会话、轮次与可操作性校验。
`tests/unit/test_session_actor.py` 覆盖无传输依赖的多观察端互斥、不同会话并行、持久队列清洗、
恢复幂等与停止保留输入；Web 回归继续覆盖 FIFO、立即执行、会话快照与工作区编辑。
前台与后台检查由同一生命周期包装器释放槽位。前台首时隙前停止会把尚未执行的输入
放回队列并通知原请求；已进入执行的回合不自动重放。清理期间仍占用槽位，迟到收尾
不能清除新回合；队列在这一窗口预留一个恢复位置，总上限仍为 20。
`tests/unit/test_session_capacity.py` 验证运行、检查清理、优先输入、FIFO 和确认中的会话
不被淘汰；稳定 SID 会话必须先保存成功，工厂/恢复失败保留原会话及资源。
容量已满且无安全候选时拒绝新会话，WebSocket 返回 agent_error 并以 1013 关闭，连接
和订阅统一清理。临时 ws-id 会话继续只驻内存；这是单进程状态保护，不是磁盘断电保证。
明确删除空闲会话时，actor 同时清理优先输入与停止标记，防止同 SID 重建执行已删除的
输入；仍在运行或取消清理的会话拒绝删除。
`tests/unit/test_session_event_stream.py` 覆盖快照/订阅竞态、多观察端顺序、进程恢复时过滤
临时事件、断线与存储失败隔离，以及有界回放。事件持久化失败仍记录告警并继续内存广播，
不宣称每条事件均可靠落盘；同会话发送仍串行，慢观察端的背压隔离尚未实现。
`desktop/src/tasks/taskFeed.test.ts` 覆盖实时事件优先于在途旧 HTTP 快照、并发请求乱序、
连接切换、重置代次、工作区快照、重复任务 ID 和 HTTP 失败降级。TaskFeed 由 useTasks
每实例持有，App 保留唯一协议分发入口；任务状态不由客户端计算。
`desktop/src/connection/sessionConnection.test.ts` 验证并发握手、旧连接事件与失败、待连接时
断开以及迟到断开结果；`TasksPanel.test.tsx` 用真实组件渲染验证下派、验收与 PR 闸门。
真实模型和打包 Desktop 的人工点击验收仍按 `DESKTOP_ACCEPTANCE.md` 单独采集。

严格预算合同与实现边界见 [有界只读检查预算](./BOUNDED_CHECK_BUDGET.md)。

交接回执缺说明、缺有效有时区时间或版本不匹配时仍按未处理展示，可由原确认操作补齐。
检查合同缺字段不使用默认预算；带既有尝试/结束标记的授权不允许重新认领。
完成及报告恢复要求用量结清且未超限，失败记录仍保存真实超额消耗与未知预留。
任务 ID、文件名与记录 ID 的一致性在 TaskLedger.load 统一校验，损坏文件不自动改写。
开发恢复通过原 `/api/tasks/{id}/resume` 创建一个持久 `dev-resume` 子任务；同来源请求只返回
该子任务，不重新排队。重启遗留的恢复 queued/running 改为 interrupted；继续从最新停止
任务显式恢复。来源归属和提交时计划摘要必须匹配，损坏记录拒绝重新创建；计划排队后
发生变化时在开发工具执行前拒绝。服务研究的回答和开发恢复沿用同一 TaskRunner。
任务 queued/running/终态及开发计划检查点必须保存成功；失败不启动下一步或广播成功。
`utils/async_ops.await_thread` 在取消时等待同步 Git/测试收尾，期间保留 Git 锁、任务槽和
工作树；创建阶段也纳入 finally 清理。暂停可能等待测试退出或原有超时，不强杀线程。
开发 worker 依据持久计划、所有块 landed 及审查阻塞状态判定 done；失败字符串不能充当
成功证据。dev-resume 终态拥有独立完成回执；旧任务保留历史，有效恢复血缘从待处理
计数中排除。原子文件替换与单进程锁不提供跨进程事务或 Git 提交/计划更新的原子保证。
`test_continuations.py`、`test_completion_inbox.py`、`test_check_reports.py`、
`test_continuation_checks.py` 和 `test_gateway_tasks.py` 覆盖这些边界以及旧合同兼容。
`test_task_recovery.py` 覆盖恢复重试、重启不执行、版本冲突、存储失败、冷取消/暂停、
权威开发结果与恢复血缘的注意事项投影。
`test_async_ops.py` 覆盖重复取消、晚到异常、Git 锁保留、创建/测试阶段清理顺序和暂停槽位。

## 后台开发问题与恢复边界

仅明确 owner_session 的后台 dev/dev-resume 注入开发提问工具。agents/dev_tools.py 通过
question_factory 接受任务绑定；gateway/dev_questions.py 校验台账合同，不能反向装配模型。
问题先随 running 保存；ToolTurnYield 退出实现 Agent 和隔离工具，finally 清理未提交工作树。
只有外层 worker 清理返回后，原 TaskRunner 保存 blocked、通知并释放槽位。阶段中崩溃则
标记 interrupted 并关闭未证明清理的问题，不能恢复成已阻塞或自动执行。

HTTP `/api/tasks/{id}/answer` 与主 Agent task_answer 共用同一答复服务。回答固定 owner、
任务、问题、轮次、计划、块、分支和计划摘要；先持久保存再使用原 worker pool。回答后的
计划摘要在模型执行前再检查，项目 permissions 在回答和块执行前重载。普通 dev_resume
不能绕过等待回答的后台任务。独立 CLI/IM 和模型回合内开发没有注入此提问合同。

问答路径的计划块按原依赖顺序串行实现；每个实现 Agent 最多 16 步、关闭自动继续，
每块最多两次修复尝试。同一血缘最多两次提问、三轮执行；手动恢复继承问题历史、污点、
根任务和限制。回答仅补充信息，不能扩大工具、写入、外向授权或重置预算。以上限制不等于
跨整个计划的 token/费用硬上限；生产自动检查仍关闭。

## 只读研究依赖边界

下派入口的 depends_on 固定同会话服务研究 ID/轮次；初次保存即 waiting，不创建后台
协程或占槽。直接依赖最多 8 项、深度 8、不同前置 32、遍历 128，等待任务最多 100。
前置 done/accepted 后通过原 DispatchService.release 显式核对并保存消费版本，再进入
原 TaskRunner；主 Agent task_release 与 HTTP/任务卡共用该路径。缺失依赖记录、损坏
状态、前置版本/验收/归属或已消费祖先变化，在实际模型启动前拒绝。

推进重放返回实际现状且不执行，取消 waiting 不取消前置。前置失败只在显式推进时保存
下游 failed；消费前失败需新合同。重启保留 waiting，已推进残留任务按 interrupted
处理；问题/回答与显式返工继承消费版本及原预算。消费、验收和交接回执分别记录。
当前不自动推进，不混合开发依赖；链级累计费用预算、并行问答与跨进程事务仍未实现。
`tests/unit/test_task_dependencies.py` 覆盖合同/图边界、损坏状态、原执行池、主 Agent、
HTTP、问答、失败/重启与传递版本失效；运行层证据见 DESKTOP_ACCEPTANCE.md。

## 任务链累计执行额度

服务下派可显式设置 chain_limits 创建额度根，旧合同无此字段保持兼容。带额度前置的
子任务继承唯一根；不同额度根不能合并，继承时不能另设额度。根任务保存有界成员与
逐轮预留记录，子任务只保存固定根关联；公开投影从根重新计算，不信任客户端用量。
默认总额度为 8 项任务、12 轮、64 步配额和 1800 秒超时配额，上限分别为 32、96、
1152、57600。等待只占任务名额；实际排队前预留完整原合同的步骤与超时上限。

原 TASK_STATE_LOCK 内先保存根预留，再保存子任务或下一轮；后一步失败保留已预留额度，
同合同重试复用原预留。失败、取消、提前完成和重启均不返还；回答/返工/推进不足时
保持原轮次、问题或等待状态，不能启动模型。实际启动前再次核对成员、根合同和预留。
缺失关联或损坏记录按失败处理，不回退到无预算执行；仍复用原 TaskLedger/TaskRunner。

这是执行额度合同，不是 token/费用硬上限或实际耗时计量。原循环的步骤上限不包含
强制收尾、压缩或 provider 隐式重试；生产自动检查继续关闭。任务执行、结果验收和
交接处理状态分别保留。测试与运行证据见 DESKTOP_ACCEPTANCE.md，合同见 PRD 第 26 节。

## 执行期间失效与停止

只读下派装配 execution_check 回调；主循环只在模型/压缩请求前后、流式正文/推理与工具
准入/返回调用，不导入台账或依赖实现。CollaborationService.finish 在共享锁内再次
核对 consumed 版本，迟到结果不能通过最后提交门。失效先保存原 dependencies 的
invalidated marker，保留消费快照且不允许前置恢复后自动重新有效。

HTTP、任务卡和 Web/Desktop 主 Agent task_reconcile 共用原 DispatchService；先保存
失效，再请求原 TaskRunner 取消。取消请求不等于清理完成，槽位保留到执行器退出，
重复取消不再次打断清理。失效执行结束为 failed，问题关闭；历史 done/result/review
保留，以 result_valid=false 单独表达失效。marker 纳入交接 revision，不吞掉新通知。
检查点、显式核对及下述有界事件协调共同观察变更，不承诺全图立即停止；远端请求/同步
工具可能仍需返回，不能声称远端费用停止或退款。测试和运行证据见 PRD 第 27、29 节及
DESKTOP_ACCEPTANCE.md。

流式客户端对 StreamInterrupted 单独上抛，展示回调的普通异常仍允许生成继续；Agent
还原原执行检查异常。检查失败、读取错误、取消及正常结束均尝试关闭本地流，关闭失败
记录告警且不覆盖原失败；不能把关闭尝试当成远端停止证明。没有推理展示回调时仍检查
推理片段。TaskFeed 对服务研究事件重读权威依赖投影，不在客户端计算失效；事件读取
只保留一个在途请求和一个待刷新标记，新事件使旧读取失效后补读，连接/重置隔离保留。
客户端只负责刷新；服务端的反向发现与停止走下面的领域路径。流式与客户端范围见 PRD 第 28 节。

## 有界反向依赖协调

`task_dependents.py` 在原 TASK_STATE_LOCK 中读取一次 TaskLedger 有界快照，临时构建反向
关系并核对后丢弃。不增加持久索引、执行池、文件监听或轮询。每次最多读取 512 个目录项，
每条记录最多 262144 字节；反向遍历最多 32 个下游、8 层、128 次边访问。非 JSON 项也
消耗扫描名额；损坏、超长、符号链接、不可读目录、超界和保存失败均报告 complete=false。
扫描没有稳定分页，不能把部分发现当作全部下游已核对。

服务协作更新、主 Agent 审查适配与 TaskRunner 更新触发同一领域协调；通知传输失败不
阻止核对，嵌套更新不再次扫描当前批次。内部按前置 ID 查找，归属变化也能发现旧下游；
公共 dependents/reconcile-dependents 接口则固定来源 owner/round，只返回同 owner 的任务引用。
下游修改前重新核对自己的 owner/round，使用原 DispatchService.reconcile_dependencies；
先保存 marker 才请求原池停止，独立分支失败不会阻止其他已发现分支。

恢复后的启动核对与已开始的所属用户回合只检查消费过的合同和未收尾的失效任务。
用户回合先只读发现，无候选时不构造后台池；工作区与原池不一致则保留 unknown 提示，
不修改任一台账。General 或无任务工具入口不装配核对。等待任务的事件仅刷新原投影，
不自动 release 或调用模型。直接文件修改没有事件，需等检查点、启动、所属用户回合或
显式核对；历史 marker 改变交接 revision，但不改写 done/result/review 或自动确认交接。

complete 只表达本次发现和核对操作的覆盖，不表示执行器清理完成。测试见
`tests/unit/test_task_dependents.py`；接口和本地冻结 runtime 证据见 PRD 第 29 节及
DESKTOP_ACCEPTANCE.md。该原始路径仍只有一次有界前缀；大台账使用下述显式分页合同。

## 大台账分页覆盖

TaskLedger 的 scan_inventory/read_scan_record/load_scan/save_scan/scan_ids 通过 task_scan.py
管理原台账旁的 task_scans/ 覆盖凭据。任务正文仍只有 tasks/ 中的原记录，检查点不存结果
或另建执行池。生成态忽略规则包含 task_scans/；覆盖元数据采用限长、校验和与原子替换，
损坏或保存失败不能猜测进度。共享锁继续只有单进程保证。

清单最多 8192 个目录项，按文件名 UTF-8 字节升序；每项固定 lstat 的 dev/ino/mode/size/
mtime_ns/ctime_ns。每页最多 512 项/16 MiB，每条 256 KiB，累计正文读取 64 MiB。每页
前后及状态查询重新检查完整有界清单；新增、删除、更新或身份/轮次变化使旧覆盖 unknown，
恢复原内容也不解除已确认的失效。此有效性是观察到的元数据一致，不是跨进程文件快照，
不能证明没有发生未被观察到的临时变化或底层回滚。

全部清单读取后才构造反向图：最多 1024 个下游、8192 次边访问、8 层，再每次处理 32 项。
坏记录使图覆盖未知，不执行该批量合同；图/正文/页数预算耗尽保持 incomplete/unknown。
covered 范围是固定来源 owner/round 的同会话下游，不能扩大成整个工作区或所有会话。
discover 逐页返回引用，任务只读；reconcile 逐项重读身份、内容指纹后走原核对与停止。
仅本次操作可解释且保持原身份/输入/结果/额度的写入允许更新快照预期，其余变化停止继续。
完成核对不证明停止清理、验收、交接回执、持久交付或 Goal 完成。

请求身份固定 workspace/owner/source/round/request_id/mode/page_size；不透明游标固定序号。
只缓存最近一次输入的重放，不重复范围、消息或累计摘要。新序号、确认范围、前后清单
摘要及操作结果摘要形成有序链，保存在同一检查点；恢复后显式继续，启动不自动遍历各页。
检查点丢失/损坏或操作后保存失败，保留任务历史而不虚构页确认，需新请求重新核对。
每次最多 1024 个推进页，最多保留 32 个检查点，每个 8 MiB；不静默淘汰或扩大预算。

Desktop TaskCoverageActions 复用原任务卡、GatewayClient 与请求隔离，显式继续/恢复/检查，
不自动循环页或执行模型。useTaskAction 的 repeatable 选项和提交前连接检查只用于重复
页面动作，其他回答/回执仍保持原单次成功门。客户端检查固定来源、轮次、请求、快照 ID、
计数和完成条件，迟到响应不能写入新连接。证据见 PRD 第 30 节与 DESKTOP_ACCEPTANCE.md。

## 覆盖资料保留与显式释放

TaskLedger 的覆盖领域在 task_scans/archives/ 保留不可覆盖的历史封套；load_scan_archive、
save_scan_archive、scan_archive_ids 沿用同一台账、task_scan.py 和共享锁，不创建任务数据库
或执行池。原检查点校验复用纯 validate_state；归档只读核验验证完整收据链和连续范围。
先保留、fsync/回读核验，再按原版本显式释放活动文件；归档本身不释放容量，失败不
静默删文件。重复操作及重启复用归档合同，旧扫描释放后不能重建。

活动 32 个、历史 128 个均保持有限容量，损坏资料占用容量并显示 unknown；不提供历史
删除或从导出恢复活动状态。原 TaskCoverageActions 内增加资料管理入口，历史区域恒为
非当前覆盖；请求互斥/连接隔离沿用原 hook，导出不启动模型。保留旧轮次、旧结果与失效
证据，不能据此确认停止、验收、交接、持久交付或 Goal。显式外部变化观察已实现，
历史文件原文回验和离线 coverage-verify 共用纯有界核验，不读取/写入当前台账，也不
证明外部持久保留。显式 coverage-preserve 在调用者指定的既有本地专用目录同步/回读原文，
生成不可覆盖的有界保存凭据；coverage-preservation-verify 只读重新观察该目录。目标
目录另用非阻塞文件锁，保障本地 CLI 的容量争用；原任务共享锁仍只有单进程事务保证。
HTTP/任务卡只核对上传的两份原文，不访问凭据路径、不释放历史。显式活动释放先在
原 task_scans/retirements/ 保存/同步/回读有限的扫描退役身份，再移除活动文件；每个
scan_id 一份，最多 128 份/8192 字节。原 TaskLedger 提供 load/save/ids facade，仍使用
共享锁和原领域模块；不是第二个任务数据库或依赖索引。已保存但未完成释放的活动
文件保持原版本、占容量、投影 unknown，拒绝扫描/status/observe 推进，原合同可重试。
旧版本只显式补记，不在读取/启动时迁移。独立列表每页 16 项，固定 scan_id 升序，
只读核验与导出支持原归档不在目录的历史身份；损坏资料计入容量且不能猜归属。
退役凭据不包含原完整收据链、不证明迁移或历史保留，也不释放历史容量。下一阶段仍
需迁移记录、退役资料自身保存/再迁移及独立故障域合同，生产自动检查/推进未开启。
详细合同见 PRD 第 31 至 35 节及 TASK_DISPATCH_API.md。
