# 有界只读检查预算

已实现有界执行、持久授权/认领和可注入的会话 actor 检查桥；当前没有提交时授权界面或
生产自动调度入口。默认 LLMClient 不满足严格预算适配合同，因此自动检查保持关闭。
现有 cron 的 BudgetedLLM 保留调用间预算语义，不迁移或宣称为累计硬封顶。

## 执行边界

`src/llm/hard_budget.py` 的 HardBudgetedLLM 接受显式适配器和 CheckLimits；
`src/agents/bounded_check.py` 装配现有 MainAgent 和仓库只读工具，执行一个 plan 检查。
最大限制为 8000 个累计输入+输出 token、6 次工具调度、7 次模型请求（包括收尾），
120 秒整体执行时间；每次输出默认最多 1024 token，并收窄为当前剩余额度。
调用方可收窄限制，不能超过这些上限。没有子 Agent、写盘工具、Shell 或对外发送工具。
模型/工具步骤与请求次数分别计量，一次返回多个工具不能扩大工具预算。

env 或后续角色默认不能扩大步骤；强制收尾、提示纠偏和原生工具请求仍走同一个预算代理。
全部操作包括工具等待都在 asyncio timeout 范围内；这是协作式取消，不能强杀同步阻塞或
拒绝取消的工具。启用前还需验证实际工具的取消响应；需要强制墙钟隔离的执行需独立进程。
普通用户回合没有预算上下文，行为保持原样。

## Provider 适配合同

可信适配器必须显式声明 BudgetSupport 的四项能力，并提供：

- count_input_tokens(messages, tools)：当前固定模型的可靠输入 token 上界，包括消息框架、
  原生工具历史、推理历史和工具 schema；不是现有 context_usage/estimate_tokens 的估算。
- chat(messages, max_tokens=..., ...)：输出限制覆盖所有计费用量，包括推理 token；
  不隐式重试、切模型、追加视觉请求或降级重发。
- 每次返回精确 prompt_tokens、completion_tokens、total_tokens，均为非负整数且总量一致。

这些是需要由具体 adapter 验证的合同声明，不因设置布尔值就证明真实 provider 能力。
Generic LLMClient 没有声明。未知 adapter 在任何请求之前被拒绝。暂不支持流式、
多模态或调用中换模型，代理不透传其它模型调用方法。

每次请求在锁内先预留 input_upper_bound + output_limit，成功返回精确 usage 后才释放
未使用的预留；并发调用不能重复花费余额。模型失败、取消或缺失/无效 usage 保留预留并
终止后续调用，不猜测零消耗。违反输入上界/输出封顶合同的响应停止整个尝试，明确报错；
本地闸门不能撤回 provider 已违规产生的用量，不能据此宣称对不可信 provider 保证硬封顶。

## 结果与后续接入

成功返回 result 与 budget 快照，含请求数、工具数、已知 spent_tokens、包含未知请求预留的
committed_tokens、usage_complete 和 blocked_reason；HardBudgetError 也附带 budget 快照。不存敏感 prompt 或结果正文到计量记录，
也不估算价格。预算触发即失败，Agent 把异常转成文本的降级不能把触发状态洗成成功。

持久授权和结果版本认领已实现，检查桥在 session actor 空闲、没有前台输入或确认、没有
停止标记时调用这个执行器。当前不得因为这些模块已存在就开放生产自动检查开关。
真实 provider 计量/封顶和原生授权流程验收仍需独立证据。

## 自动验证

`tests/unit/test_hard_check_budget.py` 覆盖可靠 adapter 门、消息/schema 输入计量、预留与退款、
并发余额、输入超限、provider 违规、缺失 usage、失败/取消、流式/多模态拒绝、整体超时、
工具批次和强制收尾限制，以及真实 MainAgent 装配。测试使用声明能力的假 adapter，不是
真实 provider 费用或 token 计量验证。

## 持久授权与认领切片

`workflow/tasks/continuations.py` 将授权保存在原任务的 `continuation.entries`，按交接内容版本索引。
当前授权针对已结束且未处理的 dev / API 下派结果；提交时预授权、HTTP/UI 操作入口和
会话 actor 接续仍待装配。授权保存 owner、随机授权 ID、严格预算；预算不能在认领时扩大。
重复授权只在同版本、同预算、尚未认领时幂等返回。同一结果版本的认领、撤销或已结束记录
不允许重新授权；最多保留 20 个版本，达到上限拒绝新增，不清除历史来获得再次执行机会。

装配顺序必须是 provider 资格校验及会话空闲/停止/前台优先检查 → 持久 claim → 有界执行。
claim 写盘失败不得调用模型。claim 返回唯一 attempt_id，完成回写必须匹配 owner、授权 ID、
结果版本、attempt_id 和 claimed 状态。结果更新、手动交接处理、撤销和中断都拒绝迟到回写。
finish 只记录检查说明，不写 handoff_receipt、不改变任务执行/验收状态或 Goal。
检查报告尚不是已发送给用户的汇报证据，后续需先持久化会话输出再确认交接处理。

TaskRunner 启动恢复会将 claimed 检查记为 interrupted，通知沿用现有任务回调，不启动
模型、不返还或重建授权；调用可能已产生费用，因此禁止重启重试。写盘失败使恢复报错，
不会凭内存状态声称对账完成。authorized 尚未认领的记录保留，但当前没有恢复自动调度器。
撤销使后续认领/回写失效；正在执行的模型或工具取消仍需未来会话装配传递取消信号。

协作、收件箱和接续领域事务共用进程内 RLock；这保障单个服务进程中的并发认领，不能
作为多个进程共享同一工作区的分布式锁。台账沿用原子临时文件替换，不宣称断电 fsync 保证。

## 会话 actor 检查桥（已删除）

`workflow/tasks/continuation_checks.py`（`ContinuationChecks`）于 2026-11 删除：它从未接入生产完成事件，也没有 HTTP / 模型工具 / Desktop 入口，只在确定性测试里配假适配器使用。生产自动检查本来就关闭，删除不改变任何运行行为。原设计见 git 历史。下节的 CheckReportWriter 与 Web 的 `_persist_check_report` 仍在用。

## 原会话持久汇报与只补发

CheckReportWriter 使用原 SessionTable/session_store 保存展示 transcript。报告必须匹配
持久任务的 owner、结果版本、授权 ID、attempt_id、completed 状态、结论与完整预算；
caller 传入的任意文本不能伪装成已完成检查。会话与任务 ID 必须对应唯一存储身份，
文件名清洗别名、错误工作区、关闭会话和损坏身份字段均拒绝保存。

SessionTable.persist 现在返回实际保存成功与否；append_durable 先保存候选 transcript，
成功后再更新内存。保存失败不会插入可被误认作已持久化的消息。汇报仅进入展示历史，
保留原模型 history、计划、前台队列、活动和用户会话标题；结果来源标记为 tainted。
report_id 由所属会话/任务/版本/尝试确定，重复提交和进程重建会沿用同一身份，保留历史
窗口内不重复追加。展示历史仍沿用最近 200 条上限，不承诺无限期保存每份报告。

Web 的 _persist_check_report 适配保存后才广播 agent_emit；tainted/check_report 是协议的
新增可选字段。广播失败不抹掉持久汇报，重启和重连通过已有 agent_history 回放；保存
回执证明可恢复的会话输出，不证明用户已经阅读或认可。该适配不注册自动模型调度。

## 会话生命周期保护

前台与检查复用同一任务登记和收尾包装器；检查所属会话在执行或取消清理期间不能因
容量满而被淘汰。优先输入、FIFO 和待确认状态同样保护原会话。稳定 SID 的空闲会话
只有保存成功后才能被替换；没有安全候选时拒绝新会话，已有会话继续可用。

前台首时隙前停止会保留尚未执行的输入并发回原请求的取消通知；已开始的输入不自动
重放。清理仍占用唯一槽位，重复停止更新最终原因，迟到收尾不能清除新回合。
这是单进程生命周期边界；临时会话、普通输入保存失败、内存优先项和断电恢复不在
持久保证内，也不补足严格 provider 或生产授权入口的资格。

## 持久预算与交接记录完整性

ContinuationService 只接受字段齐全的持久预算合同，缺字段不使用 CheckLimits 默认值。
授权回放、待认领查询和认领共同拒绝已有尝试/用量/结束标记的 authorized 记录，不覆盖
原 attempt。授权与尝试必须为有效、匹配的身份；结束和补发不会让空 ID 匹配空记录。

完成检查必须提供完整预算：usage_complete=true、blocked_reason 为空、已知消耗等于
预算承诺，并未超过授权 token 上限。completed_report 和汇报 writer 同样拒绝未结清、
阻塞或超限的旧 completed 数据。整数预算校验不会被 Python 的 True == 1 绕过。
失败/取消仍保留未知请求预留；provider 合同违规的真实超额用量也保留在失败记录中，
不封顶抹去消耗、不返回成功报告、不确认原交接。

原完成收件箱只把有具体说明、有效有时区时间和匹配版本的回执当成已处理；不完整回执
保持未读并允许原确认入口补齐。TaskLedger 在共享读取边界核对精确任务身份与文件名，
损坏或别名查询不触发恢复、认领或改写其他任务。校验失败不自动重跑检查。
