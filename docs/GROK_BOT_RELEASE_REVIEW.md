# Grok Bot 发布包复核与本项目采用顺序

2026-11-01。目标是用户提供的 Grok Bot 0.78.1 发行包分析材料，和此前的 xAI grok-build
源码参考分开。复用 REA 已有证据，再对两份复制发布文本核验摘要与 17 个精确锚点；不运行目标。

## 证据类型与身份

历史报告的 ASAR SHA-256 为 `e8d5e10f94f8b52cd02f696599c8e6503420ad85ceed7b41a27bbff7bff22f4d`。
本轮重新计算 main-app 与 local-exec-daemon 复制文本的摘要，均与原 manifest 一致。
可复核的文件摘要、字符偏移、锚点及原上下文摘要保存在
[证据索引](evidence/grok-bot-0.78.1-execution-review.json)，不将发布代码或大证据包复制进仓库。

实际通过 REA `inspect_analysis_view` 认证读取原 Evidence bundle：

- 父记录：`ev_ddd8b3a1051dcba4f37bd3b022b6a9abef49b896c67bfd4813de1213ee6e51f9`。
- 摘要记录：`ev_2de83b0bccc3f9df93a37a96830467e62f6e2c7e0995c7776bab4723a0492ecf`。

原 REA 只选定 package、main、两个 preload 和两个配置/知识 UI 源文件，未选择 main-app 或
local-exec-daemon。原图为 partial，有 706 application unknowns、21122 semantic unknowns。
因此下面执行/授权结论是**哈希核验后的普通文本静态追踪**，不能说已经用 REA 恢复完整执行图。
摘要视图 complete-within-view 也不表示全应用完整覆盖。11 项 native companion 完整性矛盾
继续保留为 observed-untrusted，未执行、未归因；历史签名报告的 Anysphere 信息未在本轮重验，
不从包内名称或签名报告推定与 xAI grok-build 的供应商关系。

原摘要回执与人工复核报告留在 `/private/tmp/vortocode-grok-recheck-2026-11-01/`；
原包及 3 份报告留在用户提供的 `/private/tmp/grok-bot-analysis-2026-11-01/`，临时路径不保证长期保留。

## 值得采用的机制

| 静态观察 | 本项目取舍 | 尚不能推断 |
| --- | --- | --- |
| 桌面 MCP 分支说明执行在 Bot computer，另一适配器携带 server/tool/call/agent/turn/event 上下文调后端 | 插件声明、执行宿主、账号来源与具体动作分别管理，不能把本机装好 REA 当作远端已安装 | 当前账号的真实执行位置、网络可达性、安装成功 |
| MCP timeout 提醒连接器可能已经应用请求 | 继续保留结果不明与原请求身份，不把超时判作未执行而重试 | 服务端已有持久业务幂等 |
| daemon 回包检查捕获的 channel 是否仍为当前且 proven，否则不派发 | 已核对身份还要绑定连接/启动代次，迟到回包不能借新身份执行 | 内存 seen/ack 保证跨崩溃不重复/不丢失 |
| 动作审批匹配 account scope、动作、目标或关联资源；某些带时间审批有一小时判断 | 受限委托需包含来源、目标、动作、期限与撤销，本机原 gate 保留上限 | 所有审批无条件一小时过期，或后台强制正确 |
| serverAuthoritative 分支信任上游 approval，standing 只适用其明确分类 | 不复制成远端给一个 approved 就执行；须有经过核验的受限委托 | 该分支是服务端越权漏洞，或可直接作为安全保证 |
| close 的在途等待可以达到 deadline 后返回 | 停止请求、取消通知、执行退出与业务结果分别报告，未知清理保留占用/对账状态 | close 一定代表所有后台动作完全停止 |
| 知识变更有 pending/approved/rejected 与提出者/审核者/时间，个人和共享记忆分开 | 复用原 MemoryWriter 与 PersonalSkills，增加带来源任务证据/作用域/版本差异的提案；审核入口做现有领域投影 | 使用哪种向量库、embedding/RAG，拒绝提案后真实模型行为 |
| 文件 root 未指定时可能取 Home | 本项目继续显式项目范围与原路径/沙箱规则，不默认给全 Home | 文件路径检查等于所有命令均受 OS 强隔离 |

## 对当前工程的判断

浏览器驱动、Android 已有会话、助理插件/MCP/Skill 的可插拔方向继续保留；插件只提供资源
适配，原 runtime 管理执行、确认、污点、预算、取消与领域台账。当前问题是跨端连接、身份、
凭据使用和知识变更缺少完整闭环，增加 bot 或设备操作种类不能替代这些合同。

现有记忆已经有待审、拒绝、批准与历史，个人 Skill 已按摘要批准版本。[任务技能提案首版](SKILL_LEARNING.md)
现已在待审源码串联“原交付证据→手动草稿→差异审核→停用保存→独立启用”；仅 General
个人范围，不等于真实账号的持续学习、多 bot 共享或独立双人审核已经成立。
后续扩展应保留个人/项目/bot/共享作用域与真实提出者/审核者，不增加一份通用任务数据库。
人的 owner/manager/collaborator 权限与 bot 的研究/开发职责也应分别定义，近期先保证单 owner
的真实身份与动作来源，不预先扩完整企业 RBAC。

实施顺序：

1. **本轮首个切片**：[固定运行端实例和启动代次](RESIDENT_EXECUTION_IDENTITY.md)，任务提议
   冻结目标、提交与读取复核、原请求跨代次不自动重发。不是受限授权已经完成。
2. **仍需补齐**：收窄原管理员凭证委托，绑定已核验目标、动作、来源、期限/撤销与原请求身份。
   [资源总览](SESSION_RESOURCES.md)已在待审源码展示会话资源的位置、观察状态和时间，未补齐受限凭证。
3. **本次后续切片**：[个人技能学习审核](SKILL_LEARNING.md)提供来源与版本绑定、差异、拒绝、
   中断核对和独立启用；无自动监听/学习循环，记忆与跨 bot 共享审核仍保留原边界。
4. **独立验收**：一项真实项目完整交付，分别验证拒绝、取消、重启、断连和结果不明；
   账号开放情况、服务器权限、真实模型费用与 native helper 仍不能从静态代码推导。

本轮未登录、发送任务、调用 Grok/模型服务、读取个人数据/凭证、安装软件或执行发布包。
本项目代码测试也不代表 Grok 或已安装 VortoOne 的运行验收。
