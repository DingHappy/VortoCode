# 能力缺口修复路线（对标 Claude Code / Codex / opencode）

> 记录于 2026-06。对标主流编码 agent 的能力边界，盘点 VortoCode 的差距、已修复项、以及
> 剩余方向（含为什么暂未做的真实原因）。主 agent 见 `src/agents/main_agent.py`。

## 一、VortoCode 已持平 / 反超

- **隔离 dev 流水线（差异化、反超）**：`(自动)分解 → 无依赖并行隔离实现 → 有依赖按拓扑序接力 →
  每块红了自修复重试 → 逐件 + 集成验证 → 开 PR`，全程不碰 main、边跑边播进度。比 CC/Codex
  的单线程循环更结构化的自主多任务执行，且真机（mimo-v2.5）端到端验证过。
- **多模态三端**：读图 / 听音频输入 / 语音回复 TTS，TUI·Web·CLI 全通（CC 仅读图）。
- **对话压缩、hooks(+matcher+生命周期)、自定义斜杠命令、AGENTS.md、plan/build 权限门、
  headless CLI(仿 `claude -p/-c`)**：与 CC 基本持平。

## 二、本轮已修复的缺口（2026-06，PR #81–#85）

| 缺口 | PR | 内容 |
|---|---|---|
| **A1 多语言测试探测** | #81 | `src/agents/test_detect.py`：按标志文件判型（npm/pnpm/yarn、cargo、go test、pytest、make）→ 隔离 dev 流水线不再写死 pytest、对非 Python 仓库也成立 |
| **B1 联网 web_fetch** | #82 | `src/agents/web_fetch.py`：读公网 URL，stdlib urllib + SSRF 防护 + 逐跳重定向校验 + 封顶/超时 + HTML→正文，三端 |
| **C1 MCP·CLI** | #83 | 抽共享 `src/agents/mcp_tools.py`；CLI `--mcp`；TUI 复用去重 |
| **C1b MCP·Web** | #84 | 网页 opt-in（`env VORTOCODE_WEB_MCP=1`）+ 会话淘汰时关停 → **MCP 三端齐** |
| **D1 细粒度权限** | #85 | `.vortocode/permissions.yaml` deny 规则；`MainAgent._run_tool` 最优先硬拦；三端 |

> 之前已补：通用 shell、hooks、headless CLI、多模态全端、自定义命令、项目指令、
> 语义导航(jedi)、长对话压缩、只读子 agent 委派、grep 上下文、edit_file replace_all。

## 三、剩余方向（**都被基础设施 / 平台卡住，非不愿做**）

### B2 — web_search（联网搜索）
- **缺口**：agent 不能搜索（只有 B1 的按 URL 抓取）。CC/Codex 有 WebSearch。
- **卡点**：中转站实测 4 个搜索端点全 **404**（`/web_search` `/search` `/responses` `/tools/web_search`），
  且无外部搜索 API key → **没有干净后端**。
- **解法**：① 提供搜索 API（Brave/Bing/SerpAPI 等）key + base，即可接 `web_search` 工具并真机验证；
  ② 退而求其次爬 DuckDuckGo HTML（`html.duckduckgo.com/html/?q=`）——无需 key，但**脆**（结构易变、
  易被限流）、CI 无法联网测，不推荐作为主路。

### A3 — 多语言语义导航（LSP）
- **缺口**：`find_definition / find_references / rename_symbol` 只支持 Python（jedi）。
  CC/opencode 经 LSP 支持任意语言。
- **卡点**：jedi 是**纯 Python 库**才能进 CI；TS/Go/Rust 等要跑**真的 LSP server 二进制**
  （typescript-language-server / gopls / rust-analyzer），CI 里没装、测不了、且 stdio JSON-RPC 进程脆。
- **解法**：在装好对应 LSP server 的环境里实现 + 真机验证；用 `multilspy` 类封装统一 spawn/JSON-RPC。
  按语言分多个 PR、每个都需对应 server 在场。

### E1 — OS 级沙箱
- **缺口**：`run_command` / 测试执行靠 worktree 隔离 + `is_dangerous` 黑名单 + 逐条确认 + #85 权限，
  **没有真正的系统级沙箱**。Codex 用 Seatbelt(macOS)/Landlock(Linux) 限 syscall/文件/网络。
- **卡点**：平台相关、工程量大；且上述多层已兜底，全自主跑任意命令才迫切 → 紧迫性最低。
- **解法**：可先做 macOS 单平台版（`sandbox-exec` 包 `run_command`），价值边际、平台单一。

### F — 外围（低优先）
GitHub 深度（PR 评论 / issue / 读 CI 日志）、后台长时任务管理、代码 checkpoint/回退、IDE 扩展
（不建议——偏离 TUI/Web/CLI 定位）。

## 四、结论与下一步选项

**能干净落地 + 可测的 parity 缺口已清完。** 剩余三个主项都需要我当前拿不到的外部基础设施
（搜索后端 / LSP 二进制）或属平台相关大工程。

下一步可选：
1. **E1 沙箱**做 macOS 单平台版（能落地，价值边际、平台单一）；
2. **A3 / B2** —— 提供环境（装 LSP server / 给搜索 API key），即可实现并真机验证；
3. **告一段落** —— 核心差异化（隔离 dev 流水线闭环）+ parity（多语言测试/联网/MCP/权限）已到位。
