# 职责（常设 Goal）与 IM 审批协议设计（F-4）

> 状态：设计定稿（2026-07-19，Fable 5）。实现排期见 OPC 战略 P1 与 `.vortocode/BACKLOG.md` B7 批。
> 前置事实（写设计前逐一核对过源码）：
> - `src/gateway/decisions.py`：决策队列是持久状态的确定性投影；`confirm:<cid>` 项带 `tainted`
>   标记且 `can_dismiss=False`；DecisionStore 只是"已查看"台账。
> - `src/gateway/goals.py`：Goal = 目标/约束/逐条验收（每条可挂 test/build/lint/file verifier）/
>   证据/状态机（draft→active→blocked/achieved/failed），落 `.vortocode/goals/`。
> - `src/im/bridge.py`：配对制（只服务 owner）、按钮确认走内存 Future、超时 600s=拒绝（fail-closed）。
>   **局限：确认按钮只能放行 IM 桥自己会话的确认，批不了其他端（Desktop/Web/无人值守）的待决事项。**

## 一、职责（Duty）schema

### 设计决定

1. **职责不改 Goal，引用 Goal。** Goal 语义保持"一次性合同 + 终态"；职责是**无终态的常设合同**，
   单独落 `.vortocode/duties/`。避免给 Goal 状态机加"recurring"分叉污染现有验收闭环。
2. **每次执行不实例化新 Goal。** 例行运行的产物是 Journal 台账行 + （异常时）决策项；
   只有职责升级出"需要开发介入"的工作时，才由人从决策项一键创建普通 Goal。
3. **Rule of three 仍然生效**：P1 试点（中转站值班）用"裸 cron 作业 + 检查单文件"先跑
   （B6-4/B6-5 是地基），Duty dataclass 等三个真实用例后才落库。本节记录目标形态，
   让裸跑阶段照着收集摩擦。

### 目标形态

```python
@dataclass
class EscalationPolicy:
    max_retries: int = 1            # 单次运行内重试上限
    on_failure: str = "decision"    # 失败→决策队列（kind="duty"）
    notify_im: bool = True          # 升级同时推 IM 通知（只报计数与标题，不带正文）
    silence_ok: bool = True         # 无事静默：正常完成只写 Journal 台账，不打扰

@dataclass
class Duty:
    id: str                         # duty-xxxxxxxxxx
    title: str                      # 中转站夜间值班
    charter: str                    # 职责边界：做什么、绝不做什么（进系统提示，静态注入）
    checklist_path: str             # 检查单文件（.vortocode/duties/<id>.md，HEARTBEAT.md 模式）
    schedule: str                   # cron 表达式，或 "heartbeat"
    budget_tokens_per_run: int      # 单次运行 token 封顶（接 #188 计量；超限即中止并升级）
    budget_runs_per_day: int        # 频次封顶（双保险，防调度风暴烧钱）
    escalation: EscalationPolicy
    enabled: bool = True
    owner_goal_id: str = ""         # 可选：挂靠长期 Goal（如"中转站稳定运行"）
    created: str = ""
    updated: str = ""
```

### 执行约束（不可协商）

- 职责一律走 `run_isolated_session` + `UNATTENDED_PROFILE`（fail-closed、不给出网工具）。
- 检查单内容按不可信输入处理：污点规则不豁免（检查单在仓库/工作区里，可能被污染）。
- 例行产出走 cron run lane（B6-5）：只进 Journal/通知台账，绝不追加人类会话历史。
- 预算是硬顶：token 超限中止本次运行并按 escalation 升级，不是警告。
- 部门 = 职责集合，这只是查询视图（按 Duty 分组），不引入"部门"实体。

## 二、IM 审批协议（decision id 绑定）

### 要解决的问题

OPC 的手机审批口 = 在 IM 上批**全公司决策队列**（Desktop/Web/无人值守会话的待确认、职责升级项），
而不只是 IM 桥自己会话的确认。现状二者不通。

### 协议设计

1. **一切审批动作绑定 decision id。** 按钮 payload = `(decision_id, verdict, fingerprint)`；
   自由文本永远不能触发放行（B6-6 的入站污点 + 本条，双保险）。
2. **fingerprint 防过期（沿用 git review 的 patch SHA-256 哲学）**：决策项生成时对确认正文做
   内容指纹；应答必须携带指纹，Gateway 核对当前待决项指纹一致才放行——确认内容变了/重发了，
   旧按钮 fail closed。
3. **污点确认不允许纯 IM 放行。** `tainted=True` 的确认（决策队列已标 critical）在 IM 上只渲染
   两个动作：**拒绝** / **去 Desktop 处理**（手机上看不到完整上下文与 diff，放行等于闭眼签字）。
   非污点、带有界预览的写/命令确认可以 IM 放行。这是本协议最重要的一条安全规则。
4. **超时=拒绝但不丢队列。** IM 按钮沿用 600s 超时拒绝；被拒绝/超时的实时确认按现有语义结束，
   历史型决策项（goal/task/run/duty）留在队列等 Desktop 处理。
5. **全量审计。** 每次 IM 应答写 `audit.log`：decision_id、verdict、channel、fingerprint、
   时间；不复制确认正文。
6. **传输**：IM 桥按 `runtime_inbox` 模式定期拉取各本机 Gateway 的有界决策快照（复用跨项目
   Inbox 的降级语义）；应答走新增 Gateway 端点 `POST /api/decisions/{id}/respond`
   （body: verdict + fingerprint；实时确认在 Gateway 内解到对应会话的 pending confirm）。
   新端点纳入 routes baseline 与契约测试。

### 分阶段

- **阶段 1（B7）**：只应答 `confirm:` 类（允许/拒绝）；`/inbox` 命令列队列 top N；
  goal/task/run 项只读展示 + "去 Desktop"。
- **阶段 2**：duty 升级项的"重跑/忽略"；批量已读。
- **不做**：IM 上创建/修改职责与 Goal（合同变更必须在 Desktop 全量上下文里做）。

## 三、实现顺序与测试要求

1. B6-6（allowFrom/群提及门/入站污点）是本协议的前置，先合。
2. 新端点 + fingerprint 校验 + 污点降级渲染 → 契约测试（三端矩阵补 IM 端能力行：
   `can_ask_human=True`、无 `auto_approve`）+ 单测（指纹不匹配拒绝/污点项无放行按钮/
   超时拒绝后队列仍在/审计落账）。
3. 真机验收：Telegram 或钉钉批一个 Desktop 会话的写确认、拒一个污点确认（应看到"去 Desktop"）。

安全红线复述：入站即污点、审批必须绑 id+指纹、污点不纯 IM 放行、无人值守不出网。任何实现
与本文冲突时，以更严格者为准。
