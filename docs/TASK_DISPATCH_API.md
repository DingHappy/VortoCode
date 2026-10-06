# 任务下派服务接口

状态：本地实现 v1，未部署。Gateway 的一个服务入口，复用任务台账、角色执行器与后台并发池。
调用方可以是现有 Desktop/CLI 的适配层、内部业务系统或一个新的服务入口；不需要先建立 WebSocket 对话。

## 服务范围

- 下派默认只读研究任务，或 `.vortocode/agents/*.md` 中 `tools: read` 的角色。
- 查询持久结果与任务消息，验收、请求返工、补充要求、取消。
- 后台开发任务沿用 `POST /api/tasks`；该接口运行既有隔离 dev 流水线。
- 本接口不接受 cwd、模型、权限、工具清单、回调 URL 或任意对外发送目标。
- 服务面运行在 Gateway 当前 Scratch/Project 范围内；General 返回 workspace_required。

鉴权沿用 Gateway 的 `Authorization: Bearer <VORTOCODE_API_TOKEN>`。
本地无 token 的兼容模式保持不变，非本机监听仍受已有启动限制。
目前是同一个 runtime 的受信控制方入口：session 用于归属和路由，不是用户身份认证。
持有同一管理员 token 的调用方可以选择任意合法 session；不可把此接口直接当成多租户隔离服务。

## 接口清单

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/delegations/capabilities` | 支持的角色、执行限制与开发入口 |
| POST | `/api/delegations` | 异步下派只读任务，返回 202 |
| GET | `/api/delegations?session=...` | 当前所属会话最近的下派任务，最多 100 项 |
| GET | `/api/delegations/{id}?session=...` | 查询任务合同、状态、结果、错误和消息 |
| POST | `/api/delegations/{id}/review` | 接受或请求返工 |
| POST | `/api/delegations/{id}/followup` | 增加同任务下一轮并异步执行，返回 202 |
| POST | `/api/delegations/{id}/cancel` | 取消当前轮次，包括尚未启动的排队项 |
| POST | `/api/delegations/{id}/answer` | 回答精确问题，使用下一轮预算继续原任务 |
| POST | `/api/delegations/{id}/release` | 核对已验收前置结果并显式推进同任务，返回 202 |
| POST | `/api/delegations/{id}/reconcile` | 核对当前任务消费的依赖，记录失效并请求停止，返回 202 |
| GET | `/api/delegations/{id}/dependents?session=...&round=1` | 只读发现同归属的有界下游 |
| POST | `/api/delegations/{id}/reconcile-dependents` | 有界核对下游，独立报告保存/停止请求，返回 202 |
| POST | `/api/delegations/{id}/dependency-scans` | 开始或继续一个持久分页覆盖请求，返回 202 |
| GET | `/api/delegations/{id}/dependency-scans?session=...&round=1` | 复核并恢复最近的同范围 reconcile 检查点，不推进 |
| GET | `/api/delegations/{id}/dependency-scans/{scan_id}?session=...&round=1` | 复核指定覆盖检查点的有效性与累计进度，不推进 |
| GET | `/api/task-inbox?session=...&limit=20` | 所属会话未处理的开发/服务研究终态结果 |
| POST | `/api/task-inbox/{id}/acknowledge` | 为精确结果版本记录手动处理回执 |
| POST | `/api/tasks/{id}/pause` | 等待开发任务停止并保存 paused 状态 |
| POST | `/api/tasks/{id}/resume` | 为停止的开发任务创建一次恢复，重试返回原恢复任务 |

接口提供 FastAPI OpenAPI 类型化请求模型；兼容默认启用的 `/openapi.json`。
`capabilities.version=1` 表示这个服务合同版本，不改变 WebSocket 的协议版本。
`capabilities.automatic_check` 提供严格检查能力报告：available、missing、reason、limits 和
integration。当前 available=false，integration=qualified_provider_required；默认通用
LLMClient 缺少可靠输入计量与严格适配声明，不会因普通下派完成而启动自动模型回合。
此字段不授权调用，也不新增 HTTP 检查启动接口；普通研究下派仍按原 max_steps/timeout 执行。

## 下派任务

```json
{
  "session": "service-a",
  "request_id": "order-20261003-001",
  "prompt": "分析当前项目的认证流程，给出风险与修复建议",
  "agent": "",
  "acceptance": ["定位认证入口", "给出文件证据", "列出未验证内容"],
  "max_steps": 8,
  "timeout_seconds": 300
}
```

| 字段 | 要求 |
| --- | --- |
| session | 必填，1–120 位字母、数字、点、下划线或连字符；也接受带 `sid-` 的规范身份 |
| request_id | 必填，同样的字符限制；业务请求重试时使用相同 ID |
| prompt | 必填，非空，最多 4000 字符 |
| agent | 可选，缺省是只读研究员；指定角色必须存在且为 read 类型 |
| acceptance | 可选，最多 20 条，每条为 1–500 字符的非空文本 |
| max_steps | 整数 1–12，缺省 12；现有 Agent 工具调度步骤上限，不是 token/人民币费用上限 |
| timeout_seconds | 整数 1–600，缺省 300；获得并发槽后开始计时，等待并发槽不计入 |

未知字段被拒绝，布尔值或字符串不能冒充整数。角色配置及环境变量不能扩大请求的 step 上限。
任务没有递归委派、裸 Shell、写文件或外向发送工具；使用 unattended capability profile，
继承项目 permissions，输入与结果按不可信内容处理。

返回包含 `id`、`status`、`session`、`round`、`review`、`result`、`error`、`messages`、
`limits` 和可直接轮询的 `result_url`。提交额外返回 `replayed`。
正常首次提交为 queued / round=1 / review=not_submitted。

`request_id` 在所属会话内幂等：

- 同一 ID、相同任务合同返回原任务及 `replayed=true`，不再次启动模型。
- 同一 ID、更改 prompt/角色/验收要求/限制返回 409。
- 不同会话使用相同请求 ID 产生不同任务。
- 幂等标记与任务合同在第一次落盘时一起保存；写盘失败返回 503，不执行。
- 客户端网络超时后应以同一 ID 重试；不要用新 ID 猜测原请求是否已被接收。

```bash
curl -X POST "$GATEWAY_URL/api/delegations" \
  -H "Authorization: Bearer $VORTOCODE_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"session":"service-a","request_id":"analysis-001","prompt":"分析认证流程","acceptance":["说明入口与证据"]}'
```

调用方建议按 2 秒或更长间隔轮询 result_url；不需要持续占住 HTTP 请求。
已有 `task_update` / `task_handoff` WebSocket 通知也会发布，但任务台账是结果恢复依据。
本版没有 webhook；避免由任务输出或调用参数选择外传地址。

## 查询与完成后交互

执行状态为 queued / running / done / failed / cancelled / interrupted。
`done` 表示有非空提交，`review=pending` 表示待检查，不宣称质量通过或 Goal 已达成。

审查：

```json
{"session":"service-a","round":1,"verdict":"rework","note":"缺少登录失败路径的文件证据"}
```

`verdict` 为 accept 或 rework，note 必填。同轮重复相同审查幂等，改变已记录的结论返回 409。
accepted 不得扩展工作；新工作使用新的 request_id 创建新任务。

返工或补充要求：

```json
{"session":"service-a","round":1,"message":"补充登录失败和 token 过期的处理路径"}
```

任务保持原 ID，变成 queued / round=2；从原始目标、验收要求和持久消息重建上下文。
成功结果重新进入 pending。执行中不接受补充；最多三轮。旧轮次请求返回 409，不启动重复执行。
返工请求不提供自动重试幂等 ID：调用超时后先查询当前轮次，不能盲目使用新轮次重发。

取消：

```json
{"session":"service-a","round":2}
```

当前轮次取消后可查询取消状态；重复取消同轮幂等，过期轮次不影响新执行者。
等待并发槽时取消也会落盘，不留下永久 queued。

## 恢复与并发

开发任务和下派任务共用 TaskRunner 的 semaphore，默认并发数取既有 `VORTOCODE_BG_TASKS`。
下派入口在后台活跃/排队任务达到 100 项时返回 429。
不另建任务数据库、角色队列或第二个模型执行框架。

服务启动恢复：running，以及来自本 API 的残留 queued，变为 interrupted。
已接收请求的幂等重试只返回原状态，不在重启后自动再付费执行。
调用方查询 interrupted 后，可以明确提交 followup 开始下一轮。
角色在排队期间发生权限变化时，实际执行前重新校验；改为 dev/deliver 的角色不会被放行。

当前存储模式是一个 runtime 单写进程；不支持多个进程/机器并发共享 `.vortocode/tasks`。
任务结果可恢复，不承诺模型调用或外部副作用的跨崩溃 exactly-once。

## 错误约定

| HTTP | 情况 | 调用方操作 |
| --- | --- | --- |
| 400 | 非法 session/request_id、空白合同、未知或非只读角色 | 修正请求或读取 capabilities |
| 401 | Gateway 鉴权失败 | 提供有效 Bearer 凭据 |
| 404 | 查询任务不存在或不属于指定会话 | 核对任务 ID 和 session |
| 409 | 合同幂等冲突、过期轮次、非法状态、需要 workspace | 重读任务或按 workspace_required 切换 |
| 422 | 类型、长度或未知字段不符合请求模型 | 按 OpenAPI 修正字段 |
| 429 | 后台队列满 | 稍后以原请求 ID 重试 |
| 503 | 台账写入失败 | 修复存储，查询原请求，重试不换请求 ID |

模型错误、空提交、执行超时发生在异步执行期，通过 status/error 查询；
不能仅凭 POST 的 202 判断任务成功。没有现有 UI 确认通道的角色不会被静默授权。

## 集成建议与验证

Desktop 任务面板已接入服务入口：任务类型可选择“隔离开发”或“只读研究”，研究任务可填写
角色、验收要求和执行步数。服务创建的任务卡可填写处理说明，直接记录验收、请求返工、
提交下一轮或取消排队/执行中的任务；原有交给主 Agent 的草稿入口仍保留。
同一 runtime/会话中相同研究合同的网络失败重试保留请求 ID；不同合同或范围不共用 ID。
当前重试记录仅保存在本次 Desktop 内存，不宣称跨应用重启恢复请求身份。

提交逻辑位于 `desktop/src/hooks/useTaskDispatch.ts`，表单位于 `TaskDispatchForm`，
卡片动作位于 `DispatchTaskActions`；App 只提供连接、会话和刷新适配。
项目/会话切换后旧提交响应不清空当前草稿，旧项目列表响应不覆盖新项目任务。

外部业务入口只需要存储 request_id、session、task_id：提交 → 查询 → 显示证据 →
选择接受/返工 → 查询下一轮。权限与状态校验留在本服务，不由业务入口根据对话文本推断。

`tests/unit/test_dispatch_service.py` 验证 HTTP 下派链路、幂等、结果查询、返工、
取消、共享并发池、超时、恢复、权限与预算约束；协议路由清单同时更新。
测试使用假 worker/LLM；真实 provider、原生 Desktop 点击和服务部署需要独立验收。

## 完成后的主 Agent 处理

服务研究终态结果与后台开发共用所属会话的持久 task_inbox。结果字段仍作为不可信工具
数据读取，不直接拼入用户授权。主 Agent 可用 task_status/task_review 读取和审查，
向用户汇报后用 task_acknowledge 写处理回执；下一次用户回合会提醒检查未处理结果。
HTTP 查询新增 handling（非终态为 null），任务卡分别显示验收与交接处理状态。

处理回执不把 pending 改成 accepted，验收也不自动标记 handled。返工的新轮次即使文本
相同也有新结果版本；旧确认被拒绝。审查/消息时间变化本身不使已处理交付重新未读。
主 Agent 的 task_followup 不执行服务研究返工；通过原 /followup 入口或 Desktop 服务动作
提交，继续使用原步骤/超时限制和共享执行池。没有新增自动接续或付费调用。

## 手动处理交接

业务入口和 Desktop 可以直接查询并处理完成收件箱，不必启动主 Agent 模型回合。
GET 的 session 必填，可使用 `owner` 或 `sid-owner`，limit 为 1–20，默认 20。
响应 `{ "tasks": [...] }` 中每项包含 task_id、revision、kind、status、prompt、result、error、
plan_id、branch、goal_id、next_action；服务研究另含 round、agent、review、tainted、acceptance。
文本沿用收件箱的截断与脱敏；核对完整证据仍使用原任务查询。revision 必须取自本次交付。

POST 示例（revision 为查询返回的 64 位小写十六进制版本）：

```json
{
  "session": "owner",
  "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "note": "已核对结果，并向用户说明后续安排"
}
```

note 必须非空白，最多 2000 字符；不接受未知字段或类型转换。成功返回 200：
`{ "task_id": "task-...", "revision": "...", "handled": true }`。
同版本重试幂等，保留第一次已保存的说明和时间。另一会话、非终态、已变更结果或未知
任务返回 409；输入格式错误为 400/422，保存失败为 503。失败不广播成功状态。
可以用同一版本重试网络/存储失败；409 需刷新任务，再核对新结果。

回执直接写入原任务台账，成功后只广播 task_update，不重发 task_handoff、不启动模型。
标记处理不会修改执行状态、验收结论或 Goal。GET/POST 继续使用同一 Gateway 鉴权与
Scratch/Project 范围；session 仍是归属字段，不提供多租户认证。

Desktop 在有有效未处理版本的任务卡中提供“记录交接处理”；失败保留说明并提供刷新。
任务/结果/所属会话变化会重建表单；连接变化清空旧表单，迟到响应不能改写新范围。
只有收到精确匹配的已保存回执后才刷新服务端任务；不由客户端推断验收通过。
HTTP 合同与持久化测试见 `tests/unit/test_task_inbox_api.py`。

## 执行中提问与回答

只读服务研究的前两轮会装配 ask_task_question 工具。子 Agent 缺少必要信息时，可以保存
问题、建议选项和调查进展并退出本轮执行。status=blocked 表示等待回答，不占用 TaskRunner
并发槽，不在等待期间调用模型，不进入完成收件箱。普通 followup 不接受 blocked 任务。
capabilities.questions 公布 available=true、max_questions=2、expires_seconds=86400、
answer_uses_next_round=true、answerer=owner。

任务查询返回 questions 列表；每项包含 id、task_id、round、status、question、options、
context、asked_by、answerer、created、expires_at、answer、answered_at。
期限为创建后 24 小时，过期时查询显示 expired；没有额外定时器或模型轮询。
任务卡也能显示问题、已有发现和建议答案，可编辑回答后明确点击“回答并继续”。

POST /api/delegations/{id}/answer 示例：

```json
{
  "session": "owner",
  "round": 1,
  "question_id": "question-0123456789abcdef",
  "answer": "检查测试环境，版本 v2.1"
}
```

round 使用问题创建时的轮次。answer 是 1–2000 字符的非空白文本；不接受额外权限字段。
成功返回 202 和当前任务视图，附带 answered_question_id、replayed；对应问题为 answered，
记录 resumed_round。首次回答先保存再排队，任务 round 加一；原任务 ID、角色、目标、验收
标准及每轮 max_steps/timeout_seconds 保持。总计最多三轮，最多两次问答，第三轮不再提供提问工具。

原始回答文本取摘要用于幂等判定，展示内容脱敏。同一答案（忽略首尾空白）重试返回当前状态，
不重启模型、不改写第一次答复。202 也可能是已完成或已取消任务的幂等重放，不代表再次执行。
不同答案、错误问题/会话/轮次、过期或取消返回 409；队列满为 429，存储失败为 503。
提问执行尚未释放时返回 409，保留原问题，可稍后用同一请求重试。

取消 blocked 任务会关闭未回答问题。服务重启保留 blocked；回答保存后遗留的 queued/running
恢复为 interrupted，重复回答不重跑，继续需要显式 followup 并仍占用剩余轮次。
回答仅补充信息，不授予权限；角色在回答前及实际执行前重新校验，受信控制方/session 边界同上。
任务卡、业务入口和 Web/Desktop 主 Agent 共用同一回答操作。主 Agent 先调用 task_status，
然后调用 task_answer(task_id, round, question_id, answer)；归属来自当前会话，不能指定 session。
首次成功返回 queued 和下一轮，不等待后台结果；相同回答返回实际当前状态与 replayed=true。
工具仅依据用户已明确提供的信息或核实事实回答，未知偏好/授权先询问用户。下一次用户回合
提示检查有效的所属问题，提问或完成事件本身均不自动启动主 Agent。
CLI/IM/TUI 未注入共享服务时不提供该工具。后台开发问答见下文；模型回合内委派暂停尚未开放。
`tests/unit/test_task_questions.py` 覆盖持久合同、真实模型循环（假 LLM）、HTTP 和恢复边界。
`tests/unit/test_task_answer_tool.py` 覆盖主循环的 prompt/native 回灌、动态归属、共享池、
HTTP 幂等重放、通知与终态交接、角色/权限/保存/容量边界。

## 开发暂停与单次恢复

后台开发仍使用原 `POST /api/tasks` 和隔离 dev 流水线。`pause` 等待取消清理，并在
paused 保存成功后返回任务视图；保存失败返回 503，不报告已暂停。开发任务提交、
实际执行和终态也要求保存成功，存储失败不会先执行再假报成功。
已经启动的同步 Git/测试线程须先退出，再释放锁、清理工作树和报告暂停；暂停可能等待
当前测试完成或其原有超时。协程取消不会强杀线程，重复取消也不能提前报告已停止。

`POST /api/tasks/{id}/resume` 不需要请求体，成功返回 200 和 `dev-resume` 任务视图，
包含 `parent_task_id`、`plan_id`、所属会话、目标及 `replayed`：

- 首次恢复 `replayed=false`，先保存一个确定身份的子任务，再使用原 TaskRunner 排队。
- 同一来源重试 `replayed=true`，返回该子任务当前状态，即使已完成/失败/中断也不再次执行。
- 原任务视图返回 `can_resume=false` 和 `resumed_task_id`；Desktop 定位恢复任务。
- 恢复任务再次暂停或失败时，先核对证据，再对这个最新任务显式恢复，创建下一条血缘。

来源必须是 paused/interrupted/failed/cancelled 的 dev/dev-resume，存在未 done 的持久
计划，计划保持原 `vorto/` 隔离分支。同计划有活动任务、队列满、合同不匹配或损坏记录
返回 409；未知来源为 404，写盘失败为 503。沿用管理员 runtime token；没有新增会话
身份认证。此幂等规则仅适用于恢复入口，普通 `POST /api/tasks` 不因此具备提交去重。

恢复合同固定提交时的来源与计划摘要。执行前计划被修改，恢复任务记 failed，保留原因，
开发工具不执行；核对最新计划后从失败子任务显式恢复。计划文件 ID 必须匹配请求及文件名。
服务重启把残留恢复 queued/running 标记 interrupted；旧来源重试只查询，不自动重放。
已 landed 块沿用原 dev_resume 跳过，开发计划检查点失败立即停止后续步骤。

开发完成由持久计划判定：integrated/done、所有块 landed、无审查阻塞。仍有未完成块、
集成失败或审查阻塞时 task=failed，保留工具结果与错误。task=done 不等于 Goal 达成或 PR
已发布。dev-resume 结果进入同一完成收件箱，回执与父任务独立；父任务的历史交接不自动
确认，已建立有效恢复血缘的旧暂停/失败不再重复占用注意事项计数。

状态采用原子文件替换和单进程锁。Git 提交与计划写盘仍是两个操作；在二者之间崩溃时，
恢复前需核对分支与块证据，本切片不保证跨进程或跨 Git/台账的恰好一次执行。
后台开发提问已在下节接入；未增加自动回答或外向权限。

## 后台开发提问与原任务回答

明确所属会话的 `POST /api/tasks` 开发任务及其 dev-resume 已开放结构化问答。查询中的
`development` 包含 version=1、owner_session、plan_id、root_task_id、round、questions、
tainted 和 limits。限制为 max_steps=16、max_attempts=2、max_rounds=3、max_questions=2；
它们约束实现 Agent、每块修复及问答血缘，不表示整个计划的费用硬预算。
问题除通用字段还包括 plan_id、block_id、plan_revision 和 branch。顶层 questions、session、
round 同步投影，供 HTTP 回答记录与任务卡核对。

`blocked` 仅在原隔离执行器完成未提交工作树清理后保存；等待释放原任务槽，无模型请求。
此状态不能走普通恢复；必须回答或显式取消。CLI/IM 的独立开发、模型回合内工具和无稳定
owner 的旧任务不注入该工具。问答路径的块串行执行，避免并行工作树仍活动时接受答复。

`POST /api/tasks/{id}/answer` 使用研究答复同形请求：

```json
{
  "session": "owner",
  "round": 1,
  "question_id": "question-0123456789abcdef",
  "answer": "实现测试环境的配置"
}
```

只接受上述四项严格类型字段，round 为提问时的 1 或 2，answer 为非空白且最多 2000 字符。
成功返回 202 和当前开发任务视图，附带 replayed、answered_question_id；首次任务 queued、
原任务 ID 不变、round 加一，答复先保存再排队。Web/Desktop 主 Agent 的 task_answer 使用
同一服务，所属会话由控制方注入，不接受模型传入 session、预算或权限。

同一答案忽略首尾空白后幂等，即使已 done/cancelled/interrupted 也仅返回现状；不同答案、
错归属/轮次/问题、过期、取消、计划摘要/块/分支变化、权限收紧或清理未完成返回 409。
队列容量满为 429，保存失败为 503，非法字段为 400/422。回答保存后的计划摘要在实际
执行前再次校验；答复和块执行前重载原项目权限。失败不消耗问题或启动模型。

取消 blocked 会关闭未回答问题；同一血缘最多两次问答、三轮，手动恢复继承限制与记录。
重启保留 blocked；已回答的残留 queued/running 转 interrupted，答复重放不能自动执行。
需核对证据后从最新停止任务显式恢复。提问阶段崩溃但未证明清理则关闭问题并 interrupted。
完成继续走原任务收件箱与独立处理回执，回答不构成验收、授权或 Goal 达成。
测试见 `tests/unit/test_dev_questions.py`；当前实现和后续范围见 PRD 第 24 节。

## 只读研究依赖与显式推进

POST /api/delegations 可选增加 `depends_on`，保持原 request_id 幂等入口：

```json
{
  "session": "owner",
  "request_id": "environment-difference-v1",
  "prompt": "基于配置入口研究环境差异",
  "max_steps": 8,
  "timeout_seconds": 300,
  "depends_on": [{"task_id": "task-dispatch-example", "round": 1}]
}
```

依赖仅允许同会话的现存服务研究任务，每项严格限定 task_id/round，round 为整数 1–3，
不允许重复 ID。最多 8 项直接依赖、8 层、32 个不同前置任务及 128 项遍历，拒绝环和
损坏记录。初始返回 202、status=waiting、dependencies.resolution=waiting；不启动模型
或占共享槽。依赖顺序归一化，纳入请求指纹；改变依赖必须使用新的 request_id。

`GET /api/delegations/capabilities` 的 dependencies 描述 available=true、
mode=explicit_release、max_dependencies=8、max_depth=8、max_graph_tasks=32、
waiting_tasks=100、requires_review=accepted、automatic_release=false。

推进入口（202）：

```http
POST /api/delegations/{id}/release
Content-Type: application/json
```

```json
{"session": "owner", "round": 1}
```

首次推进要求所有指定前置 done 且 review=accepted。先保存 consumed、released_round 和
inputs，再将同一任务排队；响应附 replayed=false。inputs 每项保存完整 revision、固定
轮次、500 字符以内 prompt、2000 字符以内 result、truncated 和 tainted。实际启动时
再次校验前置与已消费祖先，结果变化不会静默使用旧输入。上下文不授予新权限。

前置执行中/待验收为 409，保持 waiting。前置失败、取消、中断、返工或归属/轮次/版本
变化在本次显式操作中保存 failed、resolution=failed、error，并返回 202；不启动模型。
这类消费前失败必须重新提交合同。损坏下游合同、错归属/轮次、在途执行为 409；共享池
或等待队列满为 429，保存失败为 503，非法字段/类型为 400/422，均不声称推进成功。

同一 released_round 重放返回 replayed=true 和实际当前状态，包括完成、取消、重启中断
或后续轮次；不重新排队。等待状态可通过原 cancel 取消。重启保留 waiting；保存推进后
的残留 queued/running 转 interrupted，显式 followup 继承已消费版本及原三轮总上限。
消费不替代前置验收、交接处理或 Goal 证据；当前只在显式推进时传播前置失败。

Web/Desktop 主 Agent 可先 task_status，再用 `task_release({task_id, round})`，所属会话由
装配方注入，使用同一 DispatchService。未注入服务的 CLI/IM/TUI 不提供此工具。Desktop
表单选择前置并固定轮次，任务卡核对后推进；取消等待不取消上游。

测试见 `tests/unit/test_task_dependencies.py`，设计与后续预算范围见 PRD 第 25 节。

## 任务链累计执行额度

POST /api/delegations 可选增加完整 `chain_limits`：

```json
{
  "session": "owner",
  "request_id": "configuration-chain-v1",
  "prompt": "研究配置入口",
  "max_steps": 8,
  "timeout_seconds": 300,
  "chain_limits": {"tasks": 8, "rounds": 12, "steps": 64, "timeout_seconds": 1800}
}
```

四项均为严格正整数，最大分别为 32/96/1152/57600；不接受缺字段、布尔值或额外项。
单轮步骤/超时合同不能超过总额度。预算纳入 request_id 指纹；同请求重试不重复认领。
根预算保存在原任务记录，依赖成员自动继承唯一根，不允许合并不同根或提交 chain_limits
重设已继承额度。无预算前置之后可建立新根，但覆盖范围从本任务开始。

查询、下派和操作响应的 `chain_budget` 为只读投影：

```json
{
  "available": true,
  "mode": "execution_allowance",
  "root_task_id": "task-dispatch-example",
  "limits": {"tasks": 8, "rounds": 12, "steps": 64, "timeout_seconds": 1800},
  "used": {"tasks": 1, "rounds": 1, "steps": 8, "timeout_seconds": 300},
  "remaining": {"tasks": 7, "rounds": 11, "steps": 56, "timeout_seconds": 1500},
  "reserved_rounds": [1],
  "token_cost_hard_limit": false
}
```

used 表示累计成员和执行上限的预留，不是实测消耗。等待依赖仅占 tasks，推进/返工/答复
每轮按该任务原 max_steps/timeout_seconds 预留。任一维度不足返回 409，不改变旧状态或
消耗答案；保存失败为 503，不执行。取消、失败、重启与未用完部分均不退款。

根先保存、成员或轮次后保存；后一步失败保留预留，相同请求或轮次重试复用，不重复扣。
实际执行前核对完整根/成员与当前预留，不能丢掉链接绕过额度。损坏投影返回
available=false/error，不推导可执行。无预算旧任务的投影为 {}。

capabilities.chain_budget 提供 available=true、mode=execution_allowance、defaults、
max_limits、inherited=true、refund=false、token_cost_hard_limit=false。主 Agent 已有任务
工具共享同一合同，不新增模型可修改预算的入口。任务卡和提交表单消费这些投影。

这些是执行配额；不能据此声称限定累计 token、provider 请求或真实费用。旧自动检查
资格门继续关闭。设计见 PRD 第 26 节，回归见 tests/unit/test_task_chain_budget.py。

## 执行期间依赖失效与核对停止

已消费的依赖在每次模型/压缩请求前后、工具准入/返回与最终结果提交前重新核对。
发现失效后在原合同记录 resolution=invalidated，保留原 inputs，当前执行不能再提交
成功或消费新轮次。当前轮次的失效记录固定 task_id、owner_session、round、reason、at；
前置恢复原值不解除旧合同的失效。

显式核对入口（202）：

```http
POST /api/delegations/{id}/reconcile
Content-Type: application/json
```

```json
{"session": "owner", "round": 1}
```

响应沿用原任务形状，增加 invalidation_recorded（本次是否新保存失效）与 stop_requested
（是否请求原执行池停止）。依赖有效时两者为 false，不执行或预留新轮次。失效后若在途，
状态保持 queued/running/blocked 直到执行器清理结束；停止请求不是清理完成。重复请求
保留同一 marker，重复取消不打断正在执行的清理。等待依赖尚未消费时使用原 release。

dependencies 投影提供 result_valid、invalidated、can_reconcile、stop_pending 和 next_action。
stop_pending 表示终态尚未持久确认，可包含执行清理或终态保存失败，不能推导执行仍
活跃。存储恢复后重复核对可仅补记终态，不重新排队或调用模型。
读取发现失效尚未保存时 result_valid=false、can_reconcile=true；显式核对或执行检查点
保存 marker。done 的历史结果、原 review 保留，依赖失效不重写执行成功的历史；这些
结果不能继续验收、返工或作为有效前置。marker 改变交付 revision，旧处理回执失效。

错误使用原映射：归属/轮次/损坏合同/未消费为 409，保存失败为 503，非法字段为 422。
保存失败不请求停止或报告成功；检查点不会因此继续模型执行。有效问题不被关闭，
失效结束关闭未回答问题；没有预算退款。capabilities.dependencies 提供
execution_checkpoints=true、explicit_reconcile=true，automatic_release 仍为 false。

Web/Desktop 主 Agent task_reconcile 只接受 task_id/round，owner 由装配方实时注入。
未注入共享服务的入口不提供此工具。没有持久反向索引、自动全图停止或后台付费回合，
已经发出的 provider 请求不保证立即远端终止。单任务设计见 PRD 第 27 节，有界批量路径如下。

## 有界下游发现与批量核对

```http
GET /api/delegations/{id}/dependents?session=owner&round=1
```

来源必须属于指定会话和服务入口，轮次必须精确匹配当前整数轮次 1–3。仅发现同 owner
的直接及传递下游，不包含来源自身，也不返回结果正文。GET 不保存失效、通知或停止，
不会改变台账。读取为一次目录快照，无稳定排序/分页保证。

```http
POST /api/delegations/{id}/reconcile-dependents
Content-Type: application/json
```

```json
{"session": "owner", "round": 1}
```

POST 使用同一来源校验和发现合同；请求未知字段、字符串/布尔轮次被拒绝。每个下游在
操作前重新读取自己的 owner/round。已消费合同按原 reconcile 保存失效，再请求原池停止；
等待合同仅发布现有 readiness/failure 投影，不保存、推进、预留额度或执行模型。

批量响应示意（GET 的 tasks 不含最后两个操作标记）：

```json
{
  "source_task_id": "task-0123456789abcdef",
  "source_round": 1,
  "scanned": 3,
  "skipped": 0,
  "complete": true,
  "truncated": false,
  "scan_readable": true,
  "damaged": 0,
  "limits": {"scan_entries": 512, "record_bytes": 262144, "dependents": 32, "depth": 8, "edge_visits": 128},
  "tasks": [{"task_id": "task-fedcba9876543210", "round": 1, "status": "running", "resolution": "invalidated", "invalidation_recorded": true, "stop_requested": true}],
  "errors": []
}
```

scanned 计所有目录项，包括非 JSON；单条记录超过 262144 字节、损坏、符号链接或不可读
均不能当成有效任务。扫描最多 512 项（另探测一项判断截断），下游最多 32 个、8 层、
128 次边访问。超界、跳过记录、身份/合同损坏或保存失败均使 complete=false；damaged
可报告未选中损坏合同的数量，errors 只返回所选范围内的具体失败，避免暴露其他归属。

来源非法/归属/轮次冲突沿用 409/422 映射。下游独立失败保留在 errors，其他已发现分支
继续核对；POST 仍返回 202 和 complete=false，不能据 HTTP 成功判定全批成功。
invalidation_recorded 只表示本次新保存 marker；重试可为 false 而 resolution 仍为 invalidated。
stop_requested 是停止请求，complete=true 也不表示清理结束；以原任务终态及 stop_pending
确认持久收尾。输入快照、原结果/审查和累计额度保留，不退款、不自动确认交接。

capabilities.dependencies 增加 reverse_discovery/event_reconcile/startup_audit/turn_audit=true
以及同一 reverse_limits，automatic_release=false。服务协作/执行事件自动触发有界协调，
不依赖 WebSocket 客户端；通知传输失败也继续核对。启动恢复后及已有所属用户回合核对
消费过的合同，发现历史失效时保存 marker 并产生新的未处理交接版本，不启动后台模型。
内部事件可发现因来源归属变化而失效的旧下游；公共接口仍按来源当前 owner 限定读取。

直接修改磁盘文件没有事件，需等检查点、启动、已有所属用户回合或显式核对。不提供
文件监听、稳定分页、全图覆盖或跨进程事务保证。设计与验证见 PRD 第 29 节。

## 大台账持久分页覆盖

原 dependents/reconcile-dependents 路径仍只做单次前缀扫描；大台账使用新的扫描请求：

```http
POST /api/delegations/{id}/dependency-scans
Content-Type: application/json
```

```json
{"session":"owner","round":1,"request_id":"coverage-20261004-001","mode":"reconcile","page_size":512,"cursor":null}
```

| 字段 | 合同 |
| --- | --- |
| session、round | 来源当前归属和精确整数轮次，沿用原管理员控制面/工作区边界 |
| request_id | 必填，1–120 位字母、数字、点、下划线或连字符；扫描重试用同一身份 |
| mode | discover 或 reconcile，默认 reconcile；前者不修改任务记录 |
| page_size | 严格整数 1–512，默认 512；固定合同，不接受布尔或字符串 |
| cursor | 首次 null；继续使用响应 next_cursor 原文，不能自行拼接/递增 |

未知字段拒绝。workspace/owner/source/round/request_id 固定扫描身份，mode/page_size 改变
会返回 409。过旧、篡改、错范围或不存在的游标也返回 409；只支持最近一次输入的重放。
首次响应丢失时以同 request_id/cursor=null 重试；一旦已经推进多页，应使用状态查询
恢复当前游标，不把旧首次请求当作继续。单步重放返回 replayed=true，不再次核对或增加摘要。

响应关键字段示意：

```json
{
  "scan_id":"scan-0123456789abcdef01234567",
  "request_id":"coverage-20261004-001",
  "source_task_id":"task-source","source_round":1,
  "mode":"reconcile","page_size":512,"sequence":1,"phase":"records",
  "page_complete":true,"complete":false,"replayed":false,
  "next_cursor":"scan-0123456789abcdef01234567.1.<opaque>",
  "snapshot_valid":true,"validity":"observed_namespace_and_lstat",
  "order":"filename_utf8_bytes_ascending","observed_at":"2026-10-04T12:00:00.123456+00:00",
  "snapshot_id":"<sha256>","evidence_digest":"<sha256>","receipt_count":1,
  "coverage":{"state":"incomplete","entries_total":671,"entries_read":512,"record_bytes":237056,
    "skipped":0,"damaged":0,"graph_complete":false,"edge_visits":0,"selected":0,
    "processed":0,"reconciled":0,"failures":0,"reasons":[],
    "record_range":[0,512],"task_range":[0,0],"covered_digest":"<sha256>"},
  "tasks":[],"errors":[]
}
```

phase 为 records/process/finished；页返回不代表固定清单或图已完成。读完全部清单后才
规划下游，process 每页最多 32 项；tasks/errors 是本页操作结果，累计以 coverage 的
确认计数和摘要为准。discover 的 processed 只表示引用已返回，reconciled 恒为 0，不能
把发现完成说成核对完成。replayed 的操作标记描述原页结果，不表示本次又保存了一次。

清单文件名按 UTF-8 字节升序，包含非 JSON 项。最多 8192 项，每条最多 256 KiB，每页
最多 512 项/16 MiB，累计扫描正文 64 MiB；另有记录读取的探测字节。全部清单不足时
不猜图范围。图规划最多 1024 个下游、8192 次边访问、8 层；每页处理 32 项，最多推进
1024 页。完整清单但图预算耗尽仍 incomplete，不能把已知候选数当作真实全部下游数。
坏记录、超大或符号链接导致 unknown，reconcile 在这些情况下不进入批量操作。

每页前后/状态查询复核目录成员及 lstat 的 dev/ino/mode/size/mtime_ns/ctime_ns，记录读取
核对身份和 SHA-256。新增、删除、更新、owner/round 变化会使旧扫描 unknown、无后续游标；
来源恢复原值不解除。只允许本次核对产生且保留旧身份/输入/结果/额度的写入更新预期，
其他变化均停止继续。snapshot_valid 仅指本次未观察到清单/身份变化，不是跨进程快照、
恶意篡改防护或未来有效承诺；未观察到的临时变化/底层回滚不在此保证内。

complete=true 要求清单完整、图完整、候选页全部处理、无跳过/损坏/失败/预算耗尽且
最终复核匹配。false 时可因尚有 next_cursor 继续，也可终止为 incomplete/unknown；
reasons 区分 snapshot_changed/source_identity_changed/records_unreadable/contracts_damaged/
reconciliation_failed 和各类 budget。停止请求与执行器清理/持久终态仍按原单任务合同确认。

检查点保存在 TaskLedger 所属 task_scans/，不保存结果正文或创建新任务；最多 32 个、
每个 8 MiB，达到保留上限返回 409，不静默淘汰。snapshot_id 固定初始范围；record_range/
task_range 是同一清单/候选序列的半开确认前缀，covered_digest 为已确认 ID/owner/round
数组的 SHA-256。每次推进保存序号、前后范围、清单摘要和操作摘要，按规范 JSON
（ASCII 转义、键排序、紧凑分隔）递推 evidence_digest；检查点保留完整收据，重放不追加。
摘要是可审计的一致性记录，不是外部签名或结果验收。

GET 最近/指定检查点复核有效性并返回相同响应形状，最近不存在时为 null；不会推进页、
调用模型或修改任务。恢复请求取返回的 request_id/mode/page_size/next_cursor，身份不变。
重启保留游标，不自动运行分页；恢复导致记录变化时旧快照 unknown，需新 request_id。
检查点不可读/损坏或保存失败为 503（状态未知），不虚构进度；页内任务已写但检查点
写入失败时保留原 marker，旧指纹不匹配后 unknown，新请求可核对既有历史。

capabilities.dependencies.paged_coverage 给出支持标记、排序与上述限额。生产
automatic_check.available=false、automatic_release=false 和 provider 硬预算门不改变。
设计见 PRD 第 30 节，测试见 tests/unit/test_task_coverage.py 与 DESKTOP_ACCEPTANCE.md。

## 覆盖资料保留、历史导出与显式释放

活动检查点仍限 32 个，历史归档限 128 个/每个 8454144 字节；达到上限返回 409，
不淘汰资料。容量数为本工作区总用量，资料条目仅属于指定 owner/source，保留旧轮次。
鉴权和 General/项目范围门沿用原入口；session 是已有的 owner 合同，不新增多租户身份保证。

| 方法 | 路径（均在 `/api/delegations` 下） | 合同 |
| --- | --- | --- |
| GET | `/{task_id}/coverage-records?session=...&offset=0` | 活动列表、容量及历史资料；历史每页 16 项 |
| POST | `/{task_id}/dependency-scans/{scan_id}/archive` | `session,round,request_id,checkpoint_digest` |
| POST | `/{task_id}/dependency-scans/{scan_id}/release` | `session,round,archive_id,archive_digest,checkpoint_digest` |
| GET | `/{task_id}/coverage-archives/{archive_id}/export?session=...&round=...` | 原始 JSON 校验封套，任务与活动检查点只读 |
| GET | `/{task_id}/coverage-archives/{archive_id}/verify?session=...&round=...` | 核验存储的归档，返回历史视图 |
| POST | `/{task_id}/coverage-archives/{archive_id}/verify` | `session,round,artifact`，只读核验导出的封套 |

POST 字段严格且禁止额外字段；轮次为整数 1..3，摘要为 64 位小写 SHA-256，归档请求
身份为 1..120 个 `[A-Za-z0-9_.-]` 字符。offset 为 0..128 整数；历史页导航只用于资料
浏览，不构成第 30 节的累计图覆盖合同，不将不同历史页的覆盖范围相加。

列表返回 `owner_session,source_task_id,workspace_id,capacity,active,archives,offset,next_offset,
archive_count`。active 条目含 `checkpoint_digest,report,owner_session,historical=false`；
列表报告是上次观察，不重新检查台账的当前有效性。损坏活动文件仍计入 active_used，
unreadable_active 明示不可安全归档/释放，不泄漏无法确认归属的 ID。损坏历史条目只有
其固定 ID 和 `historical=true,current_coverage=false,verified=false,state=unknown`。

保留动作先匹配活动版本，另观察来源身份与目录变化，然后保存原检查点及该次观察。
活动进度不更新、任务不变、不释放容量。响应含归档身份/摘要、checkpoint_digest、
workspace_id、owner/source/round、request_id、archived_at、历史 report 及核验标记。
`historical=true,current_coverage=false,resumable=false` 恒定，report.next_cursor 恒为 null；
当时 complete=true 不表示当前覆盖。归档时观察到变化会保留原检查点及新的 unknown
观察，原历史不覆盖。nonce/游标元数据仅用于核验原检查点，不支持导入或恢复归档。

归档文件不可覆盖；同 request_id/合同重试 replayed=true，归档时间/内容/摘要不变，
不同版本复用身份为 409。写入临时文件后 fsync、原子替换、同步目录并回读核验；任何
失败均不删除活动文件。共享锁只有单进程一致性，仍不承诺跨进程事务或恶意回滚防护。

释放动作是显式的第二次 POST：重新读取并核验归档、固定归属、原活动版本和摘要，
再删除活动文件并同步目录。活动进度或观察记录变化为 409，需加载并保留当前版本；
损坏/缺失历史不能释放。响应包含同一历史视图及 released=true。活动已不存在时，仅在
归档完整时 replayed=true；操作途中失败可重试，不能因网络错误猜测是否已释放。
已释放扫描的旧 request_id/游标不可重新创建，使用新的扫描 request_id 才能开始。

只读核验检查封套 SHA-256、工作区/owner/source/round、原检查点摘要、收据顺序与
连续范围及累计链。导出的 kind 为 task_coverage_archive、version=1，evidence 包括
workspace_id、scope、scan_id、checkpoint_digest、checkpoint、observation、archived_at、
request_id；归档 ID 固定来源分组及操作身份。纯核验可调用
`src.gateway.task_scan.verify_archive(artifact)`，无需读取当前任务或启动 runtime。
摘要规范仍为键排序、ASCII 转义及紧凑分隔的 JSON；外层 sha256=digest(evidence)，
checkpoint_digest=digest(checkpoint)，收据链由 checkpoint.snapshot_id 逐项递推
`digest([previous_digest,receipt])`，必须等于 checkpoint.evidence_digest。

核验只证明内部一致性，不是外部签名、当前覆盖或任务停止/验收/交接/持久交付/Goal
证明。旧轮次、归属变更或来源已删除时仍可按原固定归属核验历史。浏览器导出不证明
外部持久保存；本轮无历史删除或重新导入，历史空间满时保留资料并拒绝新增。长期
迁移与外部变更观察留待下一阶段；自动检查、自动推进与 provider 权限保持原边界。
设计与验收见 PRD 第 31 节和 DESKTOP_ACCEPTANCE.md。

客户端导出保存接口的原始 JSON 文本；不可先转为 JavaScript Number 再 stringify。
lstat 的纳秒时间戳可能超过 2^53，重编码会改变历史封套与检查点摘要。浏览器只解析
归档 ID/摘要字符串作响应匹配，下载正文保持原文；独立核验仍用支持完整整数的 JSON
解析器，或将原文作为 POST artifact 的嵌套 JSON 直接发送。


### 活动范围的显式外部变化观察（PRD 第 32 节）

`POST /api/delegations/{task_id}/coverage-observations`，原鉴权与项目目录门生效。
严格 JSON 合同禁止额外字段，非空 checkpoints 最多 4 项且不能重复：

```json
{
  "session": "sid-owner",
  "workspace": "<coverage-records.workspace_id sha256>",
  "request_id": "observation-uuid",
  "checkpoints": [{
    "scan_id": "scan-<24 hex>", "round": 1,
    "checkpoint_digest": "<original active sha256>",
    "snapshot_id": "<original snapshot sha256>", "sequence": 5
  }]
}
```

各项必须绑定原活动版本。workspace/可读取项的 owner/source/round 不匹配为 409，
格式非法为 400/422；归属预检在任何保存前完成。已释放的项不重建。只读取所选
检查点，共享 16 MiB 原文字节预算（含损坏读取，最多一个溢出检测字节）；只读取
任务目录有界 namespace/lstat，不重读整个任务正文或依赖图。两次清单观察最多各
8192 项；不能将双次相等解释为持续监听、跨进程事务或内容签名。

200 响应固定回显 request_id、owner_session/source_task_id/workspace_id、observed_at，
每项原 reference 按请求顺序返回。保存成功项 state=observed/persisted=true，带新的
checkpoint_digest 和原结构 report；snapshot/sequence/evidence_digest/收据/读取和处理
计数不变。目录不可读/超限、观察间变更或与原范围不同时，report 锁存 unknown、
没有 next_cursor；已 unknown 不解除。来源增删改、归属/轮次变化由同一指纹合同捕获，
不报告具体失效任务已处理。归档资料不读写，任务正文、模型和执行池不操作。

未确认项 state=not_observed/persisted=false，无 report，reason 为：
`checkpoint_missing`、`checkpoint_unreadable`、`checkpoint_changed`、
`checkpoint_byte_budget` 或 `checkpoint_save_failed`。已确认与未确认分支可共存，非
批次原子事务；返回 requested/observed/unobserved 及 observation_state=observed/partial/
unknown。这个状态仅针对本次选择的观察，observed 的 report 仍可 incomplete/unknown；
未选项没有当前观察证据。恒为 pages_advanced=0、task_graph_reconciled=false，不证明
任务停止、验收、交接处理、持久交付或 Goal 达成。namespace 包含 stable、前后清单
摘要及 reason；limits 回显 4 项/16777216 字节/8192 目录项，checkpoint_bytes_read 计入
实际原文读取，不是任务覆盖数量或模型 token。

request_id 只关联本次响应，无独立幂等日志。响应丢失后用旧摘要重试可返回
checkpoint_changed，需显式加载最新版本再观察；不猜测先前结果、不新增扫描页或收据。
重启仅恢复已保存的原检查点。观察更新活动摘要后，旧归档仍完整但不能据其旧版本
释放活动容量，需先保留当前版本。历史始终不代表当前覆盖。

原 TaskCoverageRecords 入口显式勾选/观察与逐项状态，沿用 useTaskAction/GatewayClient
连接隔离，校验工作区/请求/原版本/快照/序号/未推进计数；匹配当前卡片扫描才同步
其 report。接口能力位于 dependencies.paged_coverage.external_observation，explicit=true。
自动模型检查、自动 release/推进/provider 权限未改变；长期历史迁移留待下一阶段。


### 历史文件的原文回验与离线核验（PRD 第 33 节）

`POST /api/delegations/{task_id}/coverage-archives/{archive_id}/verify-file`：原鉴权和项目
目录门保持。严格查询字段为 `session`、`round`（1..3）、`archive_digest` 和
`file_digest`（两个摘要均为 64 位小写十六进制）；重复、缺失、额外字段拒绝。正文是
原始归档 JSON 文件字节，Content-Type 必须 application/json，不包在 artifact 字段中，
不能先解析为 JavaScript Number 再 JSON.stringify。

先检查长度声明，再按实际 stream 累计缓冲限定 8454144 字节；超限为 413，无效长度
400、媒体类型 415、参数 422。空/非 UTF-8/BOM/重复 JSON 字段/非有限数/解析失败为
400；历史内部摘要或收据链损坏为 503；原文摘要或 workspace/owner/source/round/
archive_id/archive_digest 不匹配为 409。所有失败没有 verified=true 回执，不写台账。

成功返回原 TaskCoverageArchive 历史视图并增加：`file_digest`、`file_bytes`、
`verified_at`、`verification="uploaded_or_read_bytes"`、`expected_binding_checked`。
HTTP 固定检查字段为 archive_digest/archive_id/file_digest/owner_session/source_round/
source_task_id/workspace_id，按字段名排序回显。恒为 durable_copy_confirmed=false、
capacity_released=false、imported=false；原 historical/current_coverage/resumable 边界保留。
仅回验本次上传的副本，无活动或服务器历史读取，不需要来源当前存在；没有导入、
恢复、历史删除、容量释放或外部持久保存证明。重复请求仅产生新验证观察时间。

离线命令：

```shell
vc coverage-verify /path/to/archive.json \
  --archive-id archive-... --archive-digest <historical_sha256> \
  --workspace-id <original_workspace_sha256> --owner-session sid-owner \
  --source-task-id task-source --source-round 1 --file-digest <raw_file_sha256>
```

期望参数可省略；未提供者不纳入 expected_binding_checked，也不宣称上下文匹配。
成功输出 JSON、退出 0；读取/格式/完整性/期望值失败输出 verified=false/state=unknown、
退出 1；命令参数错误退出 2。只读取上限内的普通文件、拒绝末端符号链接/目录/FIFO，
读取中身份变化拒绝；不依赖 cwd 台账，在 env/log/provider 初始化前返回，不触网或写盘。
原文字节摘要与结构摘要分别保留，空白格式变化不等于证据结构变化。

客户端 TaskCoverageFileCheck 位于原历史区域，选择文件后显式核验；File.size/UTF-8/
身份检查、原文 SHA-256 和返回绑定校验均通过才显示成功。返回还必须匹配原扫描、
快照、累计摘要、收据序号、归档 request_id 和归档时间；换文件清除旧成功，迟到
读取/HTTP 响应受原连接和文件代次隔离。能力位于 retention.portable_file_verify；
history_release=false，128 个历史上限仍保留。下一阶段的外部持久保留/迁移记录合同
未实现，本轮回验不能当作其准入凭据，不能证明停止、验收、交接、交付或 Goal。

Desktop 冻结侧车仍只提供 server 命令，不新增 CLI 入口或运行池；离线核验沿用完整
vc/vortocode（src.cli:main，也可 python main.py coverage-verify），服务器运行包通过上述
原文 HTTP 接口验收。两种入口共用 task_scan 的同一纯核验，不提供两套业务实现。

### 显式本地保留与保存凭据核对（PRD 第 34 节）

完整 CLI（src.cli:main/vc，仍在 env/log/provider 初始化前执行）：

```shell
vc coverage-preserve /path/to/export.json --directory /existing/dedicated/store \
  --archive-id archive-... --archive-digest <original_sha256> \
  --workspace-id <original_workspace> --owner-session sid-owner \
  --source-task-id task-source --source-round 1 --file-digest <raw_sha256>
vc coverage-preservation-verify /existing/dedicated/store/<stem>.preservation.json \
  --directory /existing/dedicated/store
```

期望字段同 coverage-verify，可省略；只检查明确给定者。成功 JSON/退出 0，失败
verified=false/state=unknown/退出 1，参数错误退出 2。目标须已存在，不自动创建，
拒绝末端符号链接/非目录和 .git/.vortocode 内位置；读取来源沿用普通文件字节上限。
非阻塞目录锁遇到争用即失败；目录设备/inode、锁身份或读取期间文件身份变化不能
确认保存。本地保存不通过 HTTP 任意路径写盘，不读取或修改当前任务/覆盖台账。

stem 为 `<archive_id>.<file_digest>`；保留 `<stem>.archive.json` 原文及
`<stem>.preservation.json` 凭据。独占临时文件以无覆盖硬链接发布，清理本次临时名，
同步文件/目录并回读，旧文件不替换。目录最多 128 组，原文上限 8454144、凭据上限
65536 字节，观察 512 项加一个超限检测项；缺少凭据的中断原文、坏文件均占容量。
中断后同原文/目标重试可以补齐凭据，固定身份成功重复返回 replayed=true，同凭据
摘要/保存时间不变；已有不同原文、损坏凭据、凭据原文缺失不自动覆盖或重建。

凭据 kind=task_coverage_preservation/version=1；receipt_id 为 preserve-<24hex>，
由原 archive_id/file_digest/规范目标目录摘要固定；sha256=digest(evidence)，沿用原
键排序/ASCII/紧凑 JSON 摘要规范。evidence 固定归档/工作区/owner/source/round/scan/
checkpoint 身份、原文摘要/字节、覆盖摘要（snapshot/evidence_digest/sequence/receipt_count/
observed_at/state/snapshot_valid/complete/reasons）、有时区 preserved_at，以及 storage。
storage 固定 directory/artifact_name/directory_device/directory_inode/artifact_inode/method，
设备/inode 使用十进制字符串保留精度；method=local_file_fsync_directory_fsync_read_back。
完整原文保留全部收据和旧失效证据，凭据不能替代原文或成为第二份任务数据库。

保存返回原 TaskCoverageFileVerification 加 artifact_path/receipt_path、replayed 和
preservation。本地保存 state=synced_and_read_back；只读目录核验 state=read_back_now，
storage_observed_now=true，仅表示该次目录/文件读取。核验会检查两份文件最终身份、
固定目标路径/设备/inode及文件名，外部替换相同字节也不能解除身份变化。

`POST /api/delegations/{task_id}/coverage-archives/{archive_id}/verify-preservation`：
原鉴权与项目目录门。严格查询五项 session/round/archive_digest/file_digest/
receipt_file_digest，轮次 1..3、摘要 64 位小写十六进制，重复/额外/缺失字段拒绝。
Content-Type 为 application/json，外层仅允许两个原文字符串：

```json
{"artifact_text":"<original archive JSON text>","receipt_text":"<original preservation JSON text>"}
```

两个原文不能先解析为 Number 再 stringify；外层 JSON 只包装字符串。流入预算
17040384 字节，之后分别限定 8454144/65536；拒绝重复字段、BOM、非 UTF-8、非有限数。
超限正文 413，媒体类型 415，查询 422，无效原文/外层格式 400；原文/凭据原文摘要或
原 workspace/owner/source/round/归档版本不匹配 409；内部凭据/摘要/覆盖结构损坏或
与原文不一致 503。失败不返回成功凭据，不访问 storage.directory，不写入台账。

200 返回原文件历史核验视图及 preservation：receipt_id/receipt_digest/
receipt_file_digest/preserved_at/storage/state/storage_observed_now/
independent_failure_domain_confirmed。state=receipt_and_uploaded_bytes_consistent，
storage_observed_now=false；固定匹配上传凭据身份、时间、原文和原覆盖摘要，只证明
这两份字节相符。能力 retention.local_preservation 声明 write_entry=explicit_cli，
uploaded_receipt_verify=true，128 组/65536 字节与 history_release=false。

所有入口 durable_copy_confirmed=false、independent_failure_domain_confirmed=false、
capacity_released=false、imported=false，historical/current_coverage/resumable 保持原边界。
文件同步记录不是独立故障域、未来保留、外部签名或云端回读凭据，不能证明停止、
验收、交接处理、持久交付或 Goal。Desktop 侧车仍 server-only；任务卡在原文件回验
区域选择原文及凭据，显式核对，严格绑定响应与两个文件代次、原连接/任务/轮次。
换文件清除旧成功，丢失响应可重试；本轮无历史删除、导入、历史容量释放或自动推进。

## 35. 扫描退役身份

原 POST `/{task_id}/dependency-scans/{scan_id}/release` 输入不变。归档/活动版本核对后，
先不可覆盖地保存并同步/回读退役凭据，再移除活动文件。200 的 `retirement` 为核验视图。
旧版本已释放的扫描可显式重放原归档合同补记；按 scan_id 幂等，时间/摘要不更新。
128 个退役记录或目录观察超限、保存/同步/回读失败不宣称释放；已经保存但移除失败时
扫描/status/observe 不再推进该检查点，读取容量仍计数，需原合同重试。没有自动补记。

GET `/{task_id}/dependency-scans/{scan_id}/retirement?session=...&round=1` 只读核验原凭据，
固定工作区/owner/source/round，不读取当前任务或原归档；原凭据缺失/跨域 409，损坏 503。
视图 state=retired，含 retirement_id/retirement_digest、原身份/请求/模式/页大小、绑定的
归档/检查点摘要、coverage 摘要、archived_at/retired_at/reason 和可只读导出的 artifact。
kind=task_coverage_retirement，version=1，sha256 为 evidence 的规范 JSON SHA。
integrity=sha256_and_identity_binding；不宣称核验了原完整收据链。historical=true、
current_coverage=false、resumable=false、history_capacity_released=false、durable_copy_confirmed=false。

records.capacity 增加 retired_used/retired_limit=128。活动和可读历史条目增加 retirement，
state 为 retired/not_recorded/unknown。退役后残留活动的 report 只投影 unknown、不保存
投影，保留原版本供重试释放。普通列表/核验不写凭据；损坏退役凭据保留并继续占容量。
records 接受独立 retirement_offset=0..128，返回 retirements（每页 16 项、固定 scan_id
升序）、retirement_count/retirement_offset/next_retirement_offset。观察最多 128 份/1 MiB
退役记录，筛选固定 workspace/owner/source；坏记录无法确认归属，不泄露内容，只在
capacity.unreadable_retired 计数为 unknown。退役目录观察最多 256 项，再一项检测超限。
列表分页是历史身份导航，不是稳定任务图快照或当前覆盖；归档页与退役页不能累计成
已核对范围。原归档缺失仍可在独立列表只读核验和导出退役凭据。
新身份保存前预留最终目录项：已观察到 256 项时返回 409，保留残留文件和活动版本；
不是只看 JSON 记录数就继续写入。255 项可保存至最终 256 项；既有身份重放不新增项，
仍可核验/同步，不自动删除其他残留证据。
旧扫描创建前先读取固定退役身份，原归档缺失不解除退役；无凭据的旧档继续归档保护。

`vc coverage-retirement-verify <retirement.json>` 在 env/log/provider 初始化前有界只读，
接受 --scan-id/--retirement-digest 及原 workspace/owner/source/round/archive 期望绑定。
拒绝超长、非普通文件、符号链接、重复字段、非 UTF-8/BOM、非有限数字及损坏/跨域身份。
不读取原任务/归档，不导入，不恢复覆盖，不删除资料，不释放历史容量。
