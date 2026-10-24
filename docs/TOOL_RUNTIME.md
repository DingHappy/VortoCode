# 可复用工具运行层

工具兼容和 Agent 核心兼容是两个边界。当前先将工具执行从 MainAgent 抽出，现有 Agent 已使用同一实现；其它核心可以通过可信宿主适配调用。当前没有把 Codex、Claude Code 等完整核心接入 Gateway 的会话生命周期，也没有新增对外工具服务。

现有 Codex 会话的状态与轮次控制已另外抽成 [Backend 控制适配层](AGENT_BACKENDS.md)，由观察员实际调用。该控制层与本工具运行层分别接入各自原有路径，尚未把 VortoCode 工具暴露给 Codex。

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
- 工具能力检查、项目 deny、plan→build 升级、Hook、执行预算检查和审计使用同一条路径。Tool 工厂仍必须给有副作用的 handler 绑定内核 `make_confirm_gate`；build 模式和外部核心的批准不能取代这个确认门。
- `execute_batch()` 仅并行执行既有可并行只读工具；serial、有副作用和 yields_turn 工具按序执行。持久暂停后不执行后续工具。
- 外部内容及图片结果传播污点。并行批次必须由父回合 await，以把污点带回后续确认；工具被动态撤销也不抹除已摄入标记。不同异步请求处理同一回合时，执行前从回合状态恢复标记，避免 ContextVar 在子任务间不传播而漏过确认。没有媒体消费者的适配器遇到图片会明确失败，不能静默丢附件后声称成功。
- `ToolExecutionContext` / `ToolTurnState` 属于单会话当前回合，不能跨会话共享。宿主在原有回合边界重置升级、媒体和污点状态；不能在每次工具调用前清除污点。取消/撤权通过 check_execution 连接核心已有机制。
- tools supplier 每次按名称查当前工具，撤销后不能继续通过旧声明发起新调用。`execute_bound()` 只供可信内部动作复用，不接受远端传入的工具对象。

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

独立运行层测试不构造 MainAgent、不调用模型或网络，验证导出深拷贝、权限/能力拦截、确认门污点、并行/串行、暂停、取消、媒体缺失和工具撤销。现有 MainAgent、Hook、媒体、能力及预算测试覆盖生产接入回归。这些检查不代表已完成 Codex/其它真实核心的端到端接入。
