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

### B2 — web_search（联网搜索）✅ 已落地（PR #90，DuckDuckGo 爬取）
- **缺口（原）**：agent 不能搜索（只有 B1 的按 URL 抓取）。CC/Codex 有 WebSearch。
- **已做**：`src/agents/web_search.py` 走 **DuckDuckGo HTML 端点**（`html.duckduckgo.com/html/?q=`，
  **无需 API key**，用户明确选了不接搜索 API）。复用 web_fetch 的 `_urlopen` 接口 + SSRF 校验；解析
  `result__a`(标题/跳转链接) + `result__snippet`(摘要)，从 `?uddg=` 解出真实 URL；接进 `build_web_tools()`
  → 三端齐。典型：web_search 找链接 → web_fetch 深读。真机验证过（live DDG 返回干净结果）。
- **取舍**：best-effort——DDG 改版/限流可能解析不到，一律优雅降级成 '(' 说明串（不抛）。解析器用
  仿真 HTML 写了确定性单测（不触网）；live 部分 CI 不测。relay 本身无搜索后端（4 端点全 404），故不走 relay。

### A3 — 多语言语义导航（LSP）✅ TS/JS 已落地（PR #91）
- **缺口（原）**：`find_definition / find_references / document_symbols` 只支持 Python（jedi）。
- **已做**：`src/agents/lsp_client.py` —— 自写的最小 LSP 客户端（JSON-RPC over stdio，封帧抽成纯函数
  `encode_frame`/`decode_frames` 可在 CI 确定性测）+ 语言服务器登记表（按扩展名派发）。对接
  `typescript-language-server` 做 **TS/JS/TSX/JSX** 的 workspace/symbol（定义）、textDocument/references
  （引用）、documentSymbol（大纲）。`lsp.py` 的公开 `find_definition`/`find_references`/`document_symbols`
  改为 **jedi(Python) 找不到 → 回退 LSP**，工具签名不变、对 agent 透明。真机验证过（实测跨文件定义/4 处引用/类方法大纲全对）。
- **设计**：每次调用起一次性 server 子进程、查完即关（导航低频，延迟可接受）；server 未装 →
  优雅降级（提示装 / 回退 grep），绝不崩。
- **剩余**：Go(gopls)/Rust(rust-analyzer)/Java… —— 登记表加一条 + 装好二进制即可扩；
  rename 的多语言版（LSP WorkspaceEdit）暂未做，仍 jedi/Python。CI 无对应 server，行为测 skipif。

### E1 — OS 级沙箱 ✅ macOS 版已落地（PR #89）
- **缺口（原）**：`run_command` 靠 worktree 隔离 + `is_dangerous` 黑名单 + 逐条确认 + #85 权限，
  **没有真正的系统级沙箱**。Codex 用 Seatbelt(macOS)/Landlock(Linux) 限 syscall/文件/网络。
- **已做**：`src/agents/sandbox.py` —— macOS `sandbox-exec`(Seatbelt) 包 `run_command`，文件写入
  限制在仓库根 + 临时目录（写不出仓库去，挡 `rm -rf ~`/改系统文件），读/exec/网络放行（不掐 pip/git）。
  **opt-in**（env `VORTOCODE_SANDBOX=1`）默认关、零行为改变；非 macOS / 无 `sandbox-exec` 优雅降级直跑。
  真机验证过（写家目录被 Operation not permitted 挡、写仓库内放行）。三端共用 `shell.run_command` → 全覆盖。
- **剩余**：Linux 版（Landlock / bubblewrap）—— 需对应内核特性/二进制，CI 测不了，按需再做。

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
