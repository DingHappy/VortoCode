# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

VortoCode 是一个 Python 编写的 AI 编码 agent 框架：一个交互式主 agent loop，配三种前端入口（终端 TUI / 浏览器 Web / 无头 CLI）+ IM 桥接 + 无人值守隔离会话，底层共用同一 agent runtime 与一条隔离 dev 流水线。仓库注释与文档以中文为主。

## 常用命令

```bash
pip install -e '.[dev]'          # 开发依赖（ruff/mypy/black/pytest）。功能全开用 '.[all]'

# 测试
python -m pytest tests/ -q                          # 全量
python -m pytest tests/unit/test_taint.py -q        # 单文件
python -m pytest tests/unit/test_taint.py::test_x -q   # 单条用例
# asyncio_mode=auto：async 测试无需显式 @pytest.mark.asyncio

# 合并前门禁（**CI 已停用，见下**）——ruff + mypy + pytest + desktop 四道关，强制离线环境
./scripts/ci-local.sh            # 全量（unit+basic+integration+live + desktop）
./scripts/ci-local.sh quick      # 只跑 unit+basic，快一半（不含 desktop）
SKIP_DESKTOP=1 ./scripts/ci-local.sh   # 逃生口：跳过 desktop 段

# 单项检查（与门禁同口径）
python -m ruff check src tests   # 只拦真错误：E9/F63/F7/F82/F401/F403
python -m mypy                   # 范围见 pyproject [tool.mypy]，只圈 llm + 三个安全模块
black src tests                  # 格式化，line-length=100

# 运行
vc tui                           # 全屏终端主 agent（需 '.[tui]' + OPENAI_API_KEY）
vc server                        # Web 控制台，默认 127.0.0.1:8080，浏览器开 /agent
vc agent -b "任务描述"            # 无头跑一次开发任务（走隔离 dev 流水线）
vc --help                        # 全部子命令：tui/server/agent/self-*/im/cron/heartbeat/doctor…
```

**CI 状态**：`.github/workflows/ci.yml` 自 2026-07 因私有仓库额度耗尽已 `gh workflow disable`。**合并到 main 前必须本地跑 `./scripts/ci-local.sh`** 作为等价门禁。它刻意清空 `OPENAI_API_KEY/VORTOCODE_API_TOKEN/VORTOCODE_ENABLE_SHELL/ENABLE_BROWSER`，保证测试离线、确定性——别依赖你 `.env` 里的 key 让本地变绿。

**desktop 段只跑离线子集**（tsc + `cargo test --lib` + `cargo check`，约 22s），**刻意不跑 `npm run check`**：那条链里 `sidecar:build` 会 `curl` 一个 python-build-standalone 包，把网络依赖塞进本该离线的门禁。打包正确性（vite build / sidecar / bundle 冒烟）属发布前检查，仍走 `cd desktop && npm run check`；同口径的单命令版是 `npm run check:ci`。缺 node/node_modules/cargo 或 crate 冷缓存时**跳过并计入收尾的「跳过清单」**，且此时结论不会说「全部通过」——跳过 ≠ 通过。

## 架构大图

**入口**：`vc`/`vortocode` → `src/cli.py:main`（`main.py` 是薄壳）。包采用**扁平布局**：包名就是 `src`，一律 `from src.xxx import ...`。

**一个 runtime，多个前端（"三端同源"）**：TUI / Web `/agent` / 无头 CLI / IM 桥接都通过 `src/gateway/agent_session.py` 的 `build_session(kind=...)` 工厂装配同一个 `MainAgent`（`src/agents/main_agent.py`）。无人值守（cron/heartbeat）走 `src/gateway/session.py` 的 `run_isolated_session`（全新隔离上下文，不读写主会话）。跨进程 attach 走 `src/gateway/protocol.py` + `client.py` 的 WS 协议。**TUI 是刻意的例外**：它用富 UI 版工具（着色 diff + ConfirmScreen）自行装配，不走 `build_agent_tools`。

**安全内核（改动前必读——最容易踩的地方）**：安全规则**上收到内核一处**，别在每个端各写一遍（那正是"加一端漏一端"的历史病根，`tests/unit/test_three_end_contract.py` 是钉死它的能力矩阵契约表）。
- **确认门** `make_confirm_gate`（main_agent.py）：唯一的确认/放行判定。各端只声明能力（`auto_approve` 是否 headless、`can_ask_human` 是否有真人），**由内核决定问不问、能不能免**。别再往 `build_agent_tools` 塞裸 confirm。
- **污点追踪** `src/agents/taint.py`（`contextvars.ContextVar`，多会话并发互不串扰）：本回合摄入过 web/search/MCP 外部内容即"污点"，**污点态下一切免确认授权失效**（防提示注入 D0）。
- **能力档案** `src/agents/capabilities.py`：local/external/unattended 三档。`UNATTENDED_PROFILE`（cron/heartbeat）**fail-closed 且不给出网工具**（`with_web=False`）——因为无人值守下系统提示可能被本地文件污染，而 web_fetch 的 GET query 就是外传通道。
- **记忆写入策略** `src/memory/write_policy.py`：durable/proposal/quarantine/reject 四级；污点回合的指令性文本与疑似凭据不进长期记忆。
- **前缀缓存稳定**：system prompt 在**同一会话内必须字节级稳定**（静态注入，装配时读一次），否则破坏 relay 侧前缀缓存命中。仓库记忆（`src/agents/repo_memory.py`）等静态注入项都遵守这条。

**隔离 dev 流水线（护城河）**：`dev_auto` → `decompose` 拆无依赖并行批 → 每件在一次性 git **worktree**（`src/agents/worktree.py`）里 implement+自测 → 逐件+集成验证（`verify_profiles.py`/`subagent_verifier.py`）→ 落 `vorto/*` 分支 → 开 PR。**全程不碰 main / 主工作区**，委派需人工确认。`.vortocode/` 是 gitignored（本地经验：`memory/repo.md`、`agents/*.md` 自定义角色、`artifacts/` 制品）。

**LLM 网关**：所有模型调用走自建 One API 中转（OpenAI 兼容），`src/llm/`。默认走**原生 function-calling**，模型不支持则**自动回退提示式协议**；`VORTOCODE_NATIVE_TOOLS=0` 强制提示式。

## 约定与坑

- **改 Web/安全/Agent 前后必跑测试**。`tests/integration/` 是**路由契约安全网**：冻结 API 路由集合 + WS + 鉴权/执行闸/路径穿越。新增 API 路由要同步更新 `tests/integration/server_routes_baseline.json` 基线，否则契约测试红。
- 修 bug 尽量补回归测试（见 `tests/unit/test_bugfix_regressions.py`）。
- **测试必须离线、确定性**：live/联网/烧 token 的测试默认跳过；不要引入真实 API 依赖。
- **契约测试断行为、不断源码**：不要用 `inspect.getsource` 查子串来"证明"某个防护存在——那种测试会在它声称要防的回归里保持绿色（本仓已清理过一轮此类安慰剂）。
- README 里带 "已退役删除" 标注的子系统（5 角色批处理编排引擎、workspaces、独立 verification_loop、终端执行接口等）**已删除**，别照着那些历史设计写代码；当前真实能力以 README「核心特性」+ 实际代码为准。
- 对外暴露的服务默认只听 127.0.0.1；暴露前必须设 `VORTOCODE_API_TOKEN`（设了则所有 `/api/*` 与 `/ws` 强制鉴权）。命令执行走 `require_shell()` 闸，文件路径必须经 `resolve_within()` 限定在工作目录内。
