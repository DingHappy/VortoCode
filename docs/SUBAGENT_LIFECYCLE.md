# 主 agent 负责制：子 agent 的创建与生命周期（缺口分析 + ADR 草案）

状态：已决定，见 [ADR-013](DECISIONS.md)（2026-11-03）。G2 已实现，G3、G1 未实现。

## 0. 要回答的问题

主人的要求：平时只面对一个主 agent，由它为主人负责；主 agent 能按需创建合理的子 agent，
并管理子 agent 的整个生命周期。本文核对现有代码和设计能做到哪一步，列出真正缺的部分。

结论先行：方向与已定方案一致（ADR-012、[多 bot 设计 v1.4](MULTI_BOT_DESIGN.md)），**生命周期的
权威记录已经有了，不需要新建一套**。缺的是三处衔接：主 agent 不能提议新角色；后台子任务的结果
只推到 Desktop；主 agent 查看各类在办工作要用好几套工具。

参考对照：OpenMausBot 的 job runtime contract（一张 Assignment 表 + 只追加事件 + 收件箱，替换了
它原先的两套委派和六条唤醒通道）。我们没有它那种碎片化的历史包袱：进程内子任务已经统一在
TaskLedger 上，跨主机的派活单另有记录是因为它跨进程、跨宿主，这是有意的分开。

## 1. 现状核对

| 环节 | 现有实现 | 判断 |
| --- | --- | --- |
| 子 agent 从哪来 | `task` / `research_parallel` 工具（`agents/delegation.py`）；角色来自 `.vortocode/agents/*.md`（`agents/subagents.py`），未指定角色时是只读研究员 | 已有。角色文件只能由人手写 |
| 子 agent 能做什么 | 角色声明 `tools: read / deliver / dev` 三选一；子 agent 没有 `task`（不递归）、没有裸写和 shell；dev 型每次委派都过确认门 | 已有，且工具面是封闭枚举 |
| 污点 | 子回合污点经 `merge_nested_taint` 回传父回合；任务记录带 `tainted` | 已有 |
| 生命周期记录 | `workflow/tasks/collaboration.py` 的 `CollaborationService`，建在 `TaskLedger`（write-ahead，每任务一份 JSON）上：create → start → finish，轮次校验拒绝迟到结果，最多 3 轮，执行完成与验收分开（`task_review`），子任务可提问（blocked） | 已有，相当于 OpenMausBot 的 Assignment |
| 崩溃恢复 | 启动扫描把残留 running 标为 interrupted | 已有 |
| 预算 | 每回合步数；`task_chain_budget` 的根额度（ADR-008 冻结，只修 bug） | 已有，不扩 |
| 跨主机派活 | `assistant_dispatch` 任务单：proposed → 主人下发 → 常驻电脑执行；状态变化由 `assistant_watch` 推到手机 | 已有，单独记录是有意为之 |
| 共同观测 | 执行快照 v1（`agents/backends/execution.py`）已接自有 Agent、外部 CLI、Codex | 已有读合同，尚未给主 agent 一个统一入口 |
| 结果怎么回到主人 | 进程内子任务的状态变化只经 Desktop `/ws` 广播（`web/routers/realtime.py` 的 `_task_update`）；IM / App 会话装配时没有传 `on_task_update`；主 agent 在下一个用户回合用 `task_inbox` 读到 | **缺口** |

## 2. 三个缺口

### G1 主 agent 不能提议新角色

现在主 agent 只能从已有角色里选。遇到没有合适角色的工作，只能退回只读研究员，或者自己做。

拟增加 `role_propose` 工具，产出一张角色提议卡，主人批准后才写入 `.vortocode/agents/<名>.md`：

- 字段：`name`、`description`、`tools`（只能是 read / deliver / dev）、`model`（可选）、`max_steps`（有上限）、
  职责正文。工具面是封闭枚举，模型编不出新权限。
- `read` 型用普通批准；`deliver`、`dev` 型用批准钥匙（与 ADR-012 风险分级一致），且只能在 Desktop 上批。
- 污点回合不能提议（与「污点态预授权失效」同一理由：外部内容可能诱导造一个权限更大的角色）。
- 角色改动登记撤销（ADR-011 防线 5）；删除角色不影响已经在跑的任务。
- 同名角色已存在时只能提「修改」，卡片显示前后差异。

### G2 后台子任务的结果到不了手机

主人在 App 或 Telegram 里交代的工作，如果子任务转到后台、完成或卡在提问，现在没有推送，要等主人
下一次说话时主 agent 才读到。

拟让 `CollaborationService` 的状态变化走 `owner_reporter`（来源 `assistant`，即总助手的口吻），
只推三种：完成待验收、失败、等你回答。

- 推送由代码拼（任务标题、状态、下一步），不开新的模型回合。这与多 bot 设计里「完成唤醒 = 0」不冲突：
  不唤醒模型，只做一条确定性通知。
- 复用推送去重与安静时段；子任务结果文本带污点时只说「有结果待核对」，不转述内容。
- Desktop `/ws` 广播保持不变。

### G3 主 agent 查在办工作要用好几套工具

进程内子任务用 `task_status`，派活单和定时用 `assistant_watch.build_status_tools`，Codex 用另一组工具。
主 agent 要回答「现在都有哪些活在做」，得调用好几种工具再自己拼。

拟增加一个只读工具 `work_status`，数据来自执行快照 v1 加任务单记录：每项给来源、精确 id、状态、
最近更新时间、下一步由谁做。不新建存储，不替换各来源的权威记录；读不到的来源标「暂时读不到」，
不当成没有。

## 3. 不做

- 不新建生命周期数据库、调度器或编排循环（多 bot 设计 §1.1 已定）。
- 不开放子 agent 递归派活、bot 之间自由派活、自动转派（多 bot 设计 V1 默认值不变）。
- 不改 ADR-008 冻结范围：以上三项都不需要给冻结模块加功能。
- 不让子 agent 直接找主人：它的问题和结果都经主 agent 的通道出现。

## 4. ADR-013 草案

> 已按建议确认，正式文本见 `docs/DECISIONS.md` 的 ADR-013。

**ADR-013：主 agent 负责制——子 agent 只从登记角色创建，生命周期归任务服务，结果经主 agent 回到主人**

- **背景**：主人平时只面对一个主 agent（ADR-012）。委派与子任务记录已经存在，但主 agent 不能提议新角色，
  后台结果只推到 Desktop，查在办工作要用多套工具。
- **决定**：
  1. 主 agent 是主人唯一的日常对话对象；子 agent 的问题与结果都经主 agent 的对话与推送出现。
  2. 子 agent 只能按登记角色创建。新角色由主 agent 提议、主人批准；工具面限 read / deliver / dev，
     污点回合不能提议，deliver / dev 型需批准钥匙且只在 Desktop 批。
  3. 生命周期的权威记录是现有任务服务（进程内 `CollaborationService` / `TaskLedger`，跨主机为任务单）；
     主 agent 模型不持有状态，模型不可用时主人仍能查看、回答、停止。
  4. 后台子任务的完成、失败、等回答经 `owner_reporter` 推送，不唤醒模型。
  5. 主 agent 通过一个只读汇总入口查看所有来源的在办工作，各来源的权威记录不变。
- **不改**：`gate.decide()`；污点规则；子 agent 不递归；ADR-008 冻结范围；ADR-010 交接规则；
  多 bot 设计 V1 的准入默认值。
- **需要随实现更新的契约**：`tests/unit/test_three_end_contract.py`（`role_propose` 在各档案下的可用性：
  `owner_device` 只能提 read 型，`unattended` 不可用）。

## 5. 落地顺序

1. **G2 后台结果推送**：改动最小，直接改善「为我负责」的体验。
2. **G3 `work_status`**：只读，复用执行快照。
3. **G1 角色提议**：涉及批准分级与撤销，依赖 ADR-012 阶段 B 的审批卡类别（#723 等 PR 合并后再做）。

## 6. 已确认（2026-11-03）

1. 接受 ADR-013。
2. 手机可以批准 read 型角色；deliver / dev 型只在 Desktop 用批准钥匙批。
3. 顺序按 §5。

G2 实现：`src/scheduling/task_notices.py`，接在 `web/task_dispatch.get_dispatch_service` 的状态更新上。
只覆盖带协作记录的后台子任务；独立 `vc im` 进程自带的任务池不经过这里。
