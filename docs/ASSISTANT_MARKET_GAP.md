# 私人助手市场能力对照（2026-10-13）

判断：VortoCode 已有可试用的工程基础，但尚未证明达到成熟通用私人助手的端到端能力。无法用一个百分比或“落后几个月”描述差距：没有相同环境、模型、任务和权限下的竞品实测。官方能力说明也不等于所有场景稳定成功。

执行型产品参考 [Manus 官方加入 Meta 的公告](https://manus.im/en/blog/manus-joins-meta-for-next-era-of-innovation)。

| 维度 | 官方公开能力 | VortoCode 当前证据与差距 |
|---|---|---|
| 长期记忆 | [Claude 支持历史对话搜索和持续更新的主题记忆](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context) | 已有长期事实写入门、历史检索和中文匹配，本轮补日期/原文定位；冲突和更新尚依赖模型核对，中文语义检索及原生整链未验收。明显差距。 |
| 跨应用执行 | [Manus 提供应用目录、自定义 MCP 和 API 连接](https://www.manus.im/features/connectors)；[Claude 提供连接器、浏览器及屏幕交互路径](https://support.claude.com/en/articles/14128542-let-claude-use-your-computer-in-cowork) | 已有工具/MCP框架与项目任务路径，但不能由框架存在推导常用邮箱、日历、文档等完整授权及执行均已验收。应用覆盖与任务场景是主要差距。 |
| 定时与交付 | [ChatGPT workspace agents 官方案例覆盖连接器、技能、记忆、定时及交付](https://developers.openai.com/cookbook/articles/chatgpt-agents-sales-meeting-prep) | 已有调度、幂等下发、任务卡和交付证据；仍需要原生包、长时间运行、网络失联/重启恢复、真实项目与服务核对。骨架接近，成熟度未证明。 |
| 效果和成本 | 不直接采用厂商宣传成功率作横向排名 | 已完成5道ToolSandbox及20道LongMemEval校准；后者为同端点替代裁判16/20。正式留出前后对照被MiMo上游过滤阻断，未证明语义方案提高答案质量或节省实际费用。差距大小未知。 |

## 开发与验收顺序

1. 做完整的“找历史→按日期筛选→补读原文→核对来源”体验，避免长期记忆答得像对、却缺少证据。
2. 选一个日常事项贯穿任务、确认、执行、失败恢复和结果核对；先使用已授权的项目/虚拟环境，不能借本次对照开放真实外发或删除权限。
3. 做原生安装版与持续运行验收，并分别记录来源/执行/交付的证据；本地单测不算部署或真实服务成功。
4. 公共题库保持原题和固定条件，报告服务阻断及未知用量；得到完整可比较运行后，再评价质量、token、时延。

本地存储、可替换模型和任务审计是可继续建设的产品定位；它们尚不是已经优于竞品的性能证据。近期目标应收敛为少数可稳定完成、用户可核对的日常事项。
