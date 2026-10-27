# Agent 核心控制适配

方向是让助理、监督和交付逻辑复用有限的控制合同，各核心继续维护自己的上下文、工具与审批。当前只适配**已有会话**，不创建新的核心进程、任务库或执行池。

```text
常驻助理 → 人工批准 → Mac 观察员
                         │
                  control_session
                         │
               SessionControlBackend
                         │
                   CodexBackend → 已有本机 App Server
```

## 当前实现

三类执行来源现已提供 [可扩展执行合同 v1](EXECUTION_CONTRACT.md) 的只读报告。会话控制合同继续独立存在；SessionState 可携带共同快照，控制器核对来源与状态一致性。ControlReceipt 可关联执行引用，仍只代表受理。

- `SessionState`：backend/session/turn 精确身份，running/idle/unknown 状态。
- `SessionControlBackend`：read_state、start_turn、steer_turn、interrupt_turn，以及明确声明的可用操作。
- `control_session`：宿主批准后的单次控制入口。重新读状态，核对会话/轮次和支持的操作，再选择启动、补充或停止；无法确认时拒绝。不自动重试、不切备用核心、不自动创建新会话。
- `CodexBackend`：通过观察员已有 RPC 连接实现合同；状态读取只取最近一轮的身份与状态，不拉完整对话。不更改模型、工作目录、沙箱或审批策略。
- Codex 观察员的 `execute_action` 实际调用该控制入口。自动监管建议绑定产生建议的轮次；停止始终绑定原轮次。用户在自己的对话中明确要求发消息，仍可发到该会话的最新轮次。

`ControlReceipt` 只代表核心接受请求。`turn/interrupt` 返回成功不等于进程已停止，`turn/start` 返回成功不等于任务已完成；后续状态和证据需要另行核对。

## 失败与并发边界

- 先拒绝错误核心/会话状态、未知状态、缺失轮次和不支持的操作。
- Codex 补充指令携带 expectedTurnId，停止携带 turnId；不会因为旧建议错停新轮次。
- 写请求超时或回执缺失/身份不匹配，明确提示结果无法确认，不能自动重放。
- 取消向上传播，不能转换成成功回执。
- idle→start 之前虽有状态核对，协议没有通用的“上一轮必须等于 X”原子条件；不能声称完全消除了并发启动竞态。后续事件接入应继续依据核心的权威轮次身份对账。
- 此层不负责 actions.json 的持久恢复，也不把“发送未确认”自动解释成确定未送达。崩溃后的 sending 对账在 `scheduling/codex_watch.py`：认领后超过 10 分钟没有回执的动作标成 `unknown`（结果无法确认），不当成功或失败，不重发；观察员断网期间留着回执、恢复后补报，迟到的回执仍以实际结果为准。
- 宿主负责授权、后端选择和连接身份。模型输出不能选择任意宿主、修改权限或直接构造适配器。

## 与工具兼容的关系

[ToolRuntime](TOOL_RUNTIME.md) 管 VortoCode 工具的执行。Backend 管外部核心原生会话的控制。两层均已用于现有生产代码路径，但尚未连成一个可以在 Desktop 随意切换核心的产品功能。

MainAgent 仍是 Gateway 当前默认核心；Codex 自带的工具继续走 Codex 原有权限。若后续接自有核心或其它厂商核心，只实现其实际支持的控制能力，不假定跨核心上下文或审批可以直接迁移。

## 协议与验证

Codex 方法和回执语义依据 [官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)：thread/read、thread/turns/list、turn/start、turn/steer、turn/interrupt。适配器采用严格回执检查，版本不支持时明确失败。

测试使用注入的假 RPC 与另一种不含 Codex 方法名的假核心，验证共用控制器、身份边界、状态重读、回执与取消行为。测试没有向用户真实会话发指令，不代表真实核心、当前安装版本或完整 Gateway 会话切换已验收。
