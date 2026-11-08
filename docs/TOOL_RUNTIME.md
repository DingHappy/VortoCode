# 可复用工具运行层

工具兼容和 Agent 核心兼容是两个边界。当前先将工具执行从 MainAgent 抽出，现有 Agent 已使用同一实现；其它核心可以通过可信宿主适配调用。当前没有把 Codex、Claude Code 等完整核心接入 Gateway 的会话生命周期，也没有新增对外工具服务。

现有 Codex 会话的状态与轮次控制已另外抽成 [Backend 控制适配层](AGENT_BACKENDS.md)，由观察员实际调用。该控制层与本工具运行层分别接入各自原有路径，尚未把 VortoOne 工具暴露给 Codex。

```text
模型消息 / 会话 / 上下文 / 原生权限    ← Agent 核心与适配器
              │ 工具名 + 参数
              ▼
ToolRuntime：能力、deny、plan 升级、Hook、审计、调度、污点
              │
              ▼
宿主装配的 Tool.handler：原有确认门、业务规则、沙箱
```

## 当前合同

- `Tool` 是本机可信可执行对象，包含 handler 和本机策略。模型/远端只能提交名称和参数，不能提供或修改 handler、只读标记、能力档案、审批回调或运行模式。
- `tool_definitions()` 导出名称、描述和完整 `input_schema`；`function_schemas()` 转成 OpenAI 兼容 function-calling 格式。保留已有工具的参数类型、required 和旧式可选参数语义，导出结果深拷贝；声明不携带或授予本机权限。
- `ToolRuntime.execute()` 返回 `ToolOutcome(text, status, call_id)`。status 区分 succeeded、failed、blocked；执行取消向上传播，不伪装成普通文本结果。缺少工具或未知模式拒绝执行。
- `agents/tool_calls.py` 只负责模型调用信封解析。原生参数不是有效 JSON 对象、提示式批次含不合法的调用结构或重复字段时，整批先拒绝，不能把坏参数替换成 `{}` 或挑出可解析的前缀执行。原有单对象、JSON 围栏、说明文字中的对象，以及省略参数的零参数调用继续兼容；显式 null、数组和字符串参数不按空参数处理。结构通过后仍走原有工具查找和逐项执行，不提供业务批次原子性。
- MainAgent 将安全的错误码和可确定的批次位置作为运行时反馈回灌，校验反馈不附带坏参数原文；当前连续失败段最多一次格式纠正，且受原步数预算约束。纠正后仍返回坏调用、空回复或无法识别的调用格式时停止，不自动续跑 build，也不因格式错误申请执行权限。格式纠正不是工具重放或新增授权。
- 可信宿主直接调用 `execute_bound()` 时，运行层同样在能力/权限确认与 Hook 前拒绝非对象参数或非字符串键，返回同一调用 id 的 blocked 回执。此处仅校验调用结构，不新增通用 JSON Schema 校验器；必填参数、字段类型、业务规则和沙箱仍由原工具合同与 handler 负责。
- 可信 handler 可返回 `ToolResult(text, status)`，明确表达 succeeded / failed / blocked / cancelled。运行层先拆出状态，再把原文本交给媒体、截断、审计和模型；仅成功结果进入成功回调及重复调用压缩。不通过正文关键词推断状态。旧字符串和 `{text, images, ok}` 合同继续支持；未迁移的旧工具仍由原实现负责如实报告。
- `run_command` 已将参数/危险命令/沙箱/授权拦截与真实命令失败区分，`run_tests` 按原测试执行器的 `ok` 回执报告失败。后台命令的成功仅表示已启动，后续仍需 `read_output` 核对。工具调用成功不等于任务验收或部署成功。
- 工具能力检查、项目 deny、plan→build 升级、Hook、执行预算检查和审计使用同一条路径。Tool 工厂仍必须给有副作用的 handler 绑定内核 `make_confirm_gate`；build 模式和外部核心的批准不能取代这个确认门。
- `execute_batch()` 仅并行执行既有可并行只读工具；serial、有副作用和 yields_turn 工具按序执行。持久暂停后不执行后续工具。
- 外部内容及图片结果传播污点。并行批次必须由父回合 await，以把污点带回后续确认；工具被动态撤销也不抹除已摄入标记。不同异步请求处理同一回合时，执行前从回合状态恢复标记，避免 ContextVar 在子任务间不传播而漏过确认。没有媒体消费者的适配器遇到图片会明确失败，不能静默丢附件后声称成功。
- `ToolExecutionContext` / `ToolTurnState` 属于单会话当前回合，不能跨会话共享。宿主在原有回合边界重置升级、媒体和污点状态；不能在每次工具调用前清除污点。取消/撤权通过 check_execution 连接核心已有机制。
- tools supplier 每次按名称查当前工具，撤销后不能继续通过旧声明发起新调用。`execute_bound()` 只供可信内部动作复用，不接受远端传入的工具对象。

## 模型历史中的结果配对

MainAgent 将一批结果保存在原 history 的文本消息中，由 `agents/tool_results.py` 编解码。新写入格式以 `[工具 批次结果 v1]` 开头，每项使用 JSON 元数据记录工具名与正文的 Python 字符数，保留原可读标题；正文原样保存，下一项按长度定位，不扫描正文中的标题。空结果、仅空白的结果、同名多次调用、中文和 emoji 都保留原顺序与内容。

原生请求转换时，逐项核对结果数量与调用名顺序，给每个 `tool_call_id` 附上自己的正文。没有结果回执的中断历史使用明确的未知占位，不补造成功或失败，也不吞掉后续用户消息。旧标题格式继续读取；旧结果含额外标题、顺序不匹配或新格式损坏时，逐项结果保留未知，整批原文只保留一次并明确标为未配对的历史资料。

上下文折叠从解码后的真实项生成逐项占位，不再从正文提取工具名。无法确定归属的旧数据不直接折掉，保留原文与污点标记，交给原摘要路径；只有确实节省预算的候选项才计入折叠。新格式只写入原 `content`，不增加发给模型的私有消息字段，不新增数据库、路由或执行入口。恢复指纹包含格式版本，系统前缀仍不随运行结果变化。

长度和名称用于记录边界及配对，不是真实性、完整交付或验收证明。本次不改变工具执行结果的状态语义、权限、串并行调度或自动重试。新版本可读旧记录；旧版本代码无法可靠理解新格式，降级运行前需要保留快照或迁移会话资料。

## 本机参数合同的执行前检查

`Tool.argument_schema` 的显式本机约束与 `required_args` 由同一 Tool 同时导出到原生声明和提示式目录。`ToolRuntime.execute()` 与 `execute_bound()` 在能力/deny、plan 升级、PRE_TOOL_USE 和 handler 前检查；失败返回 `blocked` 和有调用 ID 的 start/finish 事件，不发起授权、不运行 Hook/handler、不计为执行成功。诊断只包含字段路径与固定说明，不回显参数值。正常调用仍受原确认门、能力、权限、沙箱和业务检查约束。

本机支持的约束为：严格 `type`（及类型列表）、`enum`、数值上下界（含 exclusive）、字符串长度、数组项数与递归 `items/properties`。`description/title/default/examples` 仅是注释，不填充默认值。整数要求实际整数，布尔值和浮点数不会隐式变成整数；所有参数必须是有限 JSON 数据。数据/合同遍历上限为深度 32、每阶段 10000 节点，复杂枚举比较也计入预算。未知本机约束、非法合同和复杂度超限均拒绝执行，不联网解析 schema，也不自动修正、删除字段或改变参数。

旧式参数默认仍可省略，未声明类型的值与额外字段保留原处理。`required_args` 是本机工具按需启用的必填字段合同，不能引用未声明参数。直接写入和隔离写入的 `write_file` 要求 `path/content`；`edit_file` 要求 `path/old/new`，路径与 old 非空，文本必须是字符串。省略正文或替换值会阻止调用；显式 `content=""` 或 `new=""` 仍可用于清空文件或删除原文，继续经过原有确认和路径检查。

边界：这是本机显式约束的有界校验，**不是完整 JSON Schema 实现**。完整 `input_schema` 维持原优先级和原样导出，其 `$ref`、组合、格式等规则仍由适配器/服务端/handler 负责；本层只检查参数为 JSON 对象和有限 JSON 数据，不将本机约束混入完整 schema。不声明类型的老工具尚未全部迁移；逐项调用被拦截不意味着整个批次原子回滚，其他合法调用仍按原调度执行。直接调用 handler 的可信宿主不会经过 ToolRuntime。未新增参数纠正循环或真实模型调用，既有步数/预算仍约束模型重试。

## 最小的文本核心适配

以下示例仅使用可信宿主提供的只读工具，不执行模型或启动额外服务：

```python
from src.agents.tool import Tool
from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime

async def inspect(args):
    return "project status"

tools = {"inspect": Tool("inspect", "Read project status", {}, inspect)}
runtime = ToolRuntime(lambda: tools, ToolExecutionContext())

# 将 runtime.definitions() 按该核心协议传给模型。
# 在宿主校验过的会话内，消费模型输出的工具调用：
result = await runtime.execute("inspect", {}, mode="plan")
# 适配器负责把 result.text/status/call_id 绑定回该核心的原调用 ID。
```

完整接入还需要由核心适配器处理请求身份、消息协议、媒体、用户确认、取消和恢复。工具运行层不能替代这些会话能力，也不会重置模型的原生沙箱。

`src/tools/registry.py` 的 Tool 是外部 MCP 声明/传输元数据；`src/agents/tool.py` 的 Tool 是经过本机装配的可执行对象。两者保留不同职责，外部声明不能直接成为可信可执行权限。

## 验证边界

独立运行层测试不构造 MainAgent、不调用模型或网络，验证导出深拷贝、权限/能力拦截、确认门污点、并行/串行、暂停、取消、媒体缺失和工具撤销。`test_tool_call_integrity.py` 用脚本模型经过两种真实请求路径，核对拒绝批次无写入、无批准和无工具事件，修正调用仍经过原确认门，以及重复错误不会触发续跑或额度升级。现有 MainAgent、Hook、媒体、能力及预算测试继续覆盖生产接入回归。这些检查不代表真实模型质量或已完成 Codex/其它真实核心的端到端接入。
