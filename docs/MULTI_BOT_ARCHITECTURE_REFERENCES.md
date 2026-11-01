# 多 bot 架构参考与采用决策

2026-10-31，针对 [设计 v1.4](MULTI_BOT_DESIGN.md) 的官方机制复核。下面只引用公开官方
文档与固定版本源码；“本项目采用”是设计决策，不表示供应商保证或本项目已实现。

## 官方资料实际说明了什么

| 来源 | 已核实的机制与边界 | 本项目采用与取舍 |
| --- | --- | --- |
| [Anthropic Research 工程文章](https://www.anthropic.com/research/multi-agent-research-system) | 领队分解明确目标/输出/工具边界，worker 用独立上下文研究再汇总；较适合可分解的广度研究，强依赖编码任务不一定合适 | 总助手协调、专职角色按需使用；不照搬其研究并行数、性能或 token 比例为本项目预算 |
| [Claude Code subagents](https://code.claude.com/docs/en/sub-agents) / [agent teams](https://code.claude.com/docs/en/agent-teams) | 角色定义、上下文与工具集合可分别配置；团队有独立会话、共享任务与消息，认领使用文件锁；团队机制仍标为实验功能 | 学角色隔离与明确认领，不复制第二套任务表；角色私聊不获得跨入口授权。文件锁不代表跨机共识，也不证明业务审批可靠 |
| [OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration) / [guardrails](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals) | handoff 转交当前对话控制，agent-as-tool 保留 manager 的答复责任；agent 级输入/输出检查不覆盖所有中间工具调用，敏感操作须在工具边界检查 | V1 取有界专职执行、总助手负责解释；不采用聊天控制转移来改变任务 owner、权限或 root。复用本项目共享 gate/tool runtime，不引入 SDK 替换内核 |
| [Google ADK Resume](https://adk.dev/runtime/resume/) | 已完成事件可恢复，未完成工具可能再次执行，属于至少一次；明确不支持停下后修改流程再直接恢复 | 检查点不当作授权或恰好一次证明；未知副作用不自动重放，流程/工具合同变化先做兼容核对 |

xAI 使用用户指定的 **grok-build**，不是 Clawdbot/OpenClaw。公开 HEAD 再次核对为
`2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8`；以下结论仅限该源码：

- [agent_quotas.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/task/coordinator/agent_quotas.rs)
  按 sender 的 agent+attempt 计消息额度，并按 target incarnation 管理在途许可；可见每次
  attempt 外发上限 32、sender→target 在途上限 4。这里不是完整业务链费用预算，不能据此
  保证本项目换 attempt 不会重获额度。V1 不开放自由 peer 消息，无需新增对应消息系统。
- [agent_targets.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/task/coordinator/agent_targets.rs)
  核对授予的发送者和目标关系；[spawn.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/agent/subagent/spawn.rs)
  将 worker 与面向用户会话分开，取消/杀停等条件会抑制完成唤醒。采用精确来源/执行代次、
  有界受理及“完成不等于继续”；不照搬其线程模型，也不开放自动唤醒。

## 结构判断

上述资料支持“协调者 + 按需专职执行”的选择；它们不证明 agent 越多质量越好，也不能
替代本项目的授权、证据与恢复合同。由这些边界推导出的项目决策是：

1. 模型提出行动，原领域决定是否执行；渠道入口只做可信路由。对话 handoff 不转移业务授权。
2. 工作者带精确合同、独立上下文和证据返回，不能用互相赞同增加权限或验收级别。
3. 控制受理与执行退出分开；SDK session、线程、检查点及内存 quota 都不是业务状态权威。
4. 多 bot 是 1+N 可选择的职责入口；总助手与一个研究 bot 是首个连通场景，不是人数上限，
   不恢复固定多角色流水线。登记数量、IM 绑定数量与实际执行并发分别控制。

## grok-build：规模与联动复核

用户明确希望不止双 bot 后，再次在线核对上述 HEAD，并继续读取以下公开实现。
本节是源码审阅，没有编译/运行 grok、创建子 Agent 或发送付费请求。

| 核对位置 | 可见机制 | 对本项目的决策 |
| --- | --- | --- |
| [官方 subagents 指南](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-pager/docs/user-guide/16-subagents.md) | 子 Agent 是独立上下文的执行会话；Agent 定义和 persona 分层，不要求每个执行者都有独立外部聊天账号 | 长期 bot 名册、职责定义、单次 worker、IM 绑定分开；有角色不等于必须新建 Telegram bot |
| [coordinator.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/task/coordinator.rs) | 单写者 actor 持有 pending/active/completed、排队、取消与回报状态，集合不是固定两个 child | 总助手模型不持有调度真相；本项目复用原领域/actor，不把 N 个 bot 变成 N 套台账或增加第二个协调循环 |
| [admission.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/task/admission.rs) | 普通子 Agent 默认每会话并发 32，可配置；满额 queue/fail；workflow 走自己的池，绕过这一普通准入 | 学“名册数量≠运行并发”，不照搬 32，也不把该值说成全部会话/workflow 的统一总上限 |
| [queue.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/task/coordinator/queue.rs) | 按到达顺序保存等待项，处理时跳过满额会话、清除取消项，让有额度的其他会话启动 | 本项目聚合原输入队列容量，跨 bot 的可运行项不被单个满额 bot 卡住；不宣称该算法本身证明严格公平或等待时延上界 |
| [spawn.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/task/coordinator/spawn.rs) / [graph.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/task/coordinator/graph.rs) | 嵌套普通 child 的准入归回根会话，保留祖先链；缺失祖先拒绝；Stopped parent 对迟到普通 spawn 关闭准入 | 后续启用嵌套时不能通过换 bot/新 child 重获根额度；本版仍不开放递归，Project 的持久取消按原 R2 门禁实现 |

增加执行者的广度不必增加派生深度。[task/mod.rs](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/task/mod.rs)
的默认深度常量为 1，工具执行前检查当前深度；宿主可注入 MaxSubagentDepth 覆盖。指南
描述的一层子 Agent 与源码保留的嵌套血缘能力要分开看，不能声称所有配置永远禁止嵌套。
本项目采用扁平 1+N 作为第一版拓扑，角色数量增加不自动允许子 bot 再创建下一层。

消息联动也不是默认自由群聊。官方指南将 `send_subagent_message` 设为可选功能，需显式
开启；sender 还要具备授予的身份。结合已核对的 agent_targets/agent_quotas，消息受具体
agent、attempt、generation、目标与数量约束。`steer/queue/interject` 表达执行中的不同
投递方式；其中打断等待不代表停止其后台工作。可恢复的已完成 child 收到消息可能再次
启动，因此本项目的结果通知暂不采用这种“发消息即唤醒执行”的语义。

这些源码证明它支持多执行者协调，不证明已经解决多个 Telegram/微信账号的长期托管。
它的 child session/agent id 也不能直接当本项目持久 bot 或绑定身份。公开普通 admission
和内存消息 quota 不等于跨进程持久业务根预算、费用硬上限或恰好一次执行；workflow 的
独立池尤其需要分别核对。我们采用职责分离、准入、血缘与消息边界，保留自己的业务门禁。

这里没有引用 Muse 的内部实现。当前材料未提供可核实的具体官方项目/版本，不用同名产品
或宣传材料补推其架构；这一证据边界不影响已确认的四家公开机制比较。

## 防止设计和审核本身无限扩张

本轮到此冻结自动协作范围，不以“再增加一个审核 agent”解决 R8—R10。审核必须给出具体
事件顺序、已有防护、剩余缺口和关闭条件；语气一致或多模型投票不是关闭证明。
下一次实施取设计切片 A/B：可信只读角色与 1+N 名册/入口共存，先以两入口跑通，再以
4/8 入口验收隔离、准入和公平性。多个桥对象的离线测试不替代同宿主实际托管证明。
C/D 继续受原门禁约束；任何新增自动转派、写入或未知外部副作用恢复均另定范围。

效果验证也先用原任务/审计记录，不建评测平台。后续在同一固定材料上选三个真实问题：
一个简单查找、一个跨文件定位、一个证据不足的问题；比较单助手与总助手＋研究者的
证据可回验程度、误报、人工介入、耗时及可取得的请求/token/费用。不给研究者更大的总
额度后声称架构本身获胜。证据不足应允许结束为待核实；没有可见收益就保留单助手路径。
这只是待执行的验收计划，本轮没有真实模型试验或性能结论。
