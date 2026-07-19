# OpenClaw 融合计划

> 上游：<https://github.com/openclaw/openclaw>（MIT，TypeScript/Node monorepo，~383k star）
> 本轮核对基线：2026-07-19 的公开 `main` 与 <https://docs.openclaw.ai/>。上游 7 万+ commit、
> 版本按 `vYYYY.M.D` 滚动，**任何直接复制发生时必须在该变更内固定具体 commit 与文件路径**。
> 服务对象：OPC 战略（见 my-knowledge `projects/vortocode/direction-opc-2026-07.md`）的
> P1 试点（中转站值班 + IM 审批闭环）与 P3 远程形态。

OpenClaw 是目前"常驻自主"范式的事实标杆（Gateway 守护进程 + 20+ 消息渠道 + heartbeat/cron），
恰好覆盖 VortoCode OPC 主轴里我们最薄弱、它最成熟的三块：**heartbeat 值班惯例、IM 渠道安全、
设备配对协议**。同时它也是治理层的反面教材（2026-01～04 共 470 条安全通告、
CVE-2026-25253 WS 劫持 RCE）——**它的通告史当作我们的免费设计审计使用**。

## 融合原则

1. **Python Gateway 仍是唯一 runtime。** 不引入 Node 常驻依赖，OpenClaw 的 TS 代码不成为第二内核；
   它的 Gateway/channel/heartbeat 与我们同构（单守护进程、WS 控制面、回环默认），融合是对齐细节，
   不是替换架构。
2. **语义移植优先。** MIT 允许直接复制，但 TS→Python 场景下多数价值在"设计与行为合同"：
   决策矩阵、配对协议、安全约定、配置 schema。真正逐行复制预计很少。
3. **治理不放宽。** OpenClaw 默认全权限本机执行、memory 自由写、入站内容不设污点——这些明确不学。
   凡是它踩过的坑，我们的落点必须先在确认门/污点/fail-closed 框架内封死再接。
4. **每次融合一小块，跟着 OPC 阶段走。** 不做"OpenClaw 兼容"，不追它的渠道数量。

## 模块映射与落点

| OpenClaw 能力 | VortoCode 落点 | OPC 阶段 | 复制方式 |
|---|---|---|---|
| Heartbeat：`HEARTBEAT.md` 检查单、`lightContext` 轻上下文、`isolatedSession`（全量 ~100K → 2-5K token/次） | `src/gateway/heartbeat.py`（已有隔离会话机制） | P1 值班 | 语义移植：值班职责=检查单文件；成本隔离思路与 `run_isolated_session` 已一致，补"无事静默"约定（无异常不产出、不打扰） |
| Cron 与 Heartbeat 的分工矩阵：精确定时/隔离用 cron，需会话上下文/模糊时点用 heartbeat；cron 例行回合走独立 run lane，**不进人类聊天 lane** | `src/gateway/cron.py` | P1 | 抄决策矩阵与 lane 语义；与我们"任务交接默认不隐式启动付费回合"原则同源，把它写成明确规则 |
| Channel 安全：`allowFrom` 白名单、群聊 @ 提及门、pairing flow、陌生 DM 默认拒绝 | `src/im/`（`bridge.py`/`channel.py`/`telegram.py`/`dingtalk.py`）、`src/gateway/im_service.py` | P1 IM 审批闭环 | 语义移植 + 配置 schema 可直接借用；**审批动作必须绑定 decision id，不接受自由文本触发外发** |
| 设备配对：nonce 质询签名、设备 token 颁发、回环自动批准 / LAN·Tailnet 显式批准 | Desktop 远程形态 / 手机审批（DESKTOP_PRODUCT_SPEC V2"远程需配对与凭据设计"的现成答案） | P3 | 抄协议设计（消息格式与状态机），实现自写 |
| Gateway 单守护进程拥有全部 channel、WS 请求-响应 + 事件推送、`{runId,status,summary}` 终态 | `src/gateway/`（协议 v9 已同构） | 印证 | 不动架构，只对齐细节：单实例独占渠道会话、断线重连语义 |
| 20+ 渠道插件（WhatsApp/Slack/Discord/飞书/微信…） | 按需单挑 | P3 后 | 只在真实痛点出现时挑**单个**渠道抄实现细节；不做渠道矩阵 |
| Skills 生态 / ClawHub / 品牌与吉祥物 | 不融合 | — | 明确排除（生态半场不进；品牌不碰） |
| 默认全权限执行、无污点入站、memory 自由写 | 明确不学 | — | 反面教材，见下节 |

## 反面教材：用它的 470 条安全通告审计我们自己

P1 接通 IM 渠道意味着**新增一个入站攻击面**，上线前对照下表逐条过：

1. **WS 面**（CVE-2026-25253 教训）：`VORTOCODE_API_TOKEN` 设置后强制鉴权、默认只听 127.0.0.1、
   Desktop token 不落 localStorage——现有边界已覆盖，IM 桥新增的任何回调/webhook 入口必须
   走同一鉴权口，不开旁门。
2. **入站即污点**：IM 渠道进来的一切内容进 `src/agents/taint.py` 污点态——污点回合免确认失效，
   这条对 IM 消息**无例外**（OpenClaw 没有这层，是它提示注入事故的根因）。
3. **审批与聊天分离**：批准/拒绝必须绑定决策中心的 decision id（结构化回调），
   自由文本永远不能直接触发外发动作或权限变更。
4. **白名单默认拒绝**：`allowFrom` 空 = 全拒；群聊必须显式 @ 才响应；陌生 DM 不进 agent。
5. **无人值守不出网**：`UNATTENDED_PROFILE` 的 `with_web=False` 不因 IM 便利而放宽；
   值班会话的 IM 通知走 Gateway 出站通道，不给值班 agent 本身出网工具。

## 直接复制代码时的许可证规则（MIT）

MIT 比 grok-build 的 Apache-2.0 更宽松（无 NOTICE/专利条款），但纪律相同：

1. 固定上游 commit 与原始文件路径，确认是上游一方代码而非 vendored 第三方；
2. 复制文件保留 MIT 版权与许可声明，文件头注明来源 commit 与 VortoCode 修改说明;
3. 登记进第三方 notices（desktop 构建已有结构化 notices 通道，runtime 侧首次复制时建立同等登记）；
4. 不复制 OpenClaw/Molty 名称、logo、吉祥物或其他品牌身份。

当前全部映射项均为"语义移植"，尚未复制任何上游源码，本轮不新增归属文件。

## 融合动作清单（跟 OPC 阶段，不单独立项）

1. **P1 值班**：`heartbeat.py` 补检查单文件约定与"无事静默"；`cron.py` 明确 run lane 规则
   （例行产出只进 Journal/通知台账，不进会话历史）。
2. **P1 IM 审批**：`src/im/` 按上表硬化（allowFrom 默认拒绝、群提及门、decision id 绑定），
   然后 Telegram/钉钉真机验证（b5 人工前置遗留项）。
3. **P3 远程**：配对协议按 nonce 质询 + 设备 token 状态机设计，先出协议文档再实现。

## 参考

- 架构：<https://docs.openclaw.ai/concepts/architecture>
- Heartbeat：<https://docs.openclaw.ai/gateway/heartbeat>
- Cron 与分工：<https://docs.openclaw.ai/automation/cron-jobs>、<https://docs.openclaw.ai/automation/cron-vs-heartbeat>
- 安全分析（第三方）：<https://arxiv.org/html/2603.27517v3>
