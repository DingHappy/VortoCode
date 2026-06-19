# VortoCode 开发总结

> 更新于 2026-06。本文档反映**当前真实状态**，已修正早期版本中夸大的"全部完成"
> 与不准确的代码统计。功能按"真实可用 / 部分实现 / 规划中"如实分级。

## 一、现状概览

| 指标 | 数值 |
|------|------|
| `src/` 子包 | 23 |
| Python 文件（src） | 106 |
| 代码行（src） | ~21,900 |
| 测试 | 155 个用例（集成/契约/安全/回归/编排/闭环/流式/沙箱/检索/自治/增量/长程编码/指标/成本/trace/预算/向量后端/分位/工作区执行/权限闸/生成器/JS-TS AST/浏览器闸/SSRF/工作目录健壮性） |
| Web API 路由 | 115（按域拆分到 19 个 router） |

## 近期新增能力

- **迭代开发闭环**：开发→测试→审查→失败反馈修复（`src/orchestrator/dev_loop.py`）。
- **Web 实时驱动**：`/api/start` 驱动 需求→架构→闭环，进度/每轮迭代/token 经 WebSocket 实时广播，前端 `index.html` 渲染。
- **LLM 流式输出**：`LLMClient.stream()` + Agent `_complete(on_token=...)`，开发阶段 token 实时上屏。
- **统一沙箱**：`src/sandbox/runner.py` 优先 Docker 真隔离，无 Docker 时按 `AUTODEV_ENABLE_SHELL` 降级宿主机，否则 fail-closed。
- **语义检索**：`code_indexer` 向量检索；回退 embedder 改为词袋哈希向量，无 key 也能按词重叠排序。
- **长目标自治**：`run_autonomous_goal()` / `AutonomousLoop` 用通用 `LLMAgent` 规划→迭代→自评进度→收敛/停止。

### 打磨批
- **Tester 隔离**：`run_pytest` 在配置了 `AUTODEV_SANDBOX_IMAGE` 且有 Docker 时容器内隔离跑 pytest（断网），否则宿主机（默认不破坏开发流程）。
- **流式节流**：`TokenBatcher` 累积到阈值/换行才广播，避免逐 token 洪泛。
- **索引增量/持久化**：`CodeIndexer` 按文件 hash 跳过未变更、仅重嵌入变更、移除已删，并持久化 hash 跨进程续用。
- **长程编码自治**：`run_autonomous_coding()` 用 LLM 把目标拆成多步，逐步在同一工作区跑迭代闭环并聚合。
- **前端时间线**：`index.html` 把 `dev_iteration` 渲染为带轮次/测试/审查徽章的时间线卡片，token 流式上屏。
- **独立时间线面板**：右侧固定可关闭面板（`#devPanel`），execution_started 显示并清空、dev_iteration 追加卡片、completed 收尾。
- **可观测/指标**：进程级全局 `MetricsCollector`（`src/core` 的 `metrics`），在 `_complete`/dev_loop 埋点（llm.calls/tokens/errors/latency、devloop.runs/iterations，带 role 标签），`/api/monitoring/metrics` 实时暴露。
- **成本追踪**：全局 `cost_tracker`（`src/models`），`_complete` 按模型 tokens×定价埋点，`/api/cost/report` 返回 total_cost/tokens/calls。
- **结构化日志/trace**：`src/core/tracing`：JSON 格式器 + `trace_id` contextvar，orchestrate/dev_loop/Web 执行起始设 id 贯穿日志；`setup_structured_logging()` opt-in。
- **指标/成本仪表盘**：`index.html` 左下浮动按钮开关的面板，轮询 `/api/monitoring/metrics` 与 `/api/cost/report` 渲染计数器/延迟分位/费用。

### 基建批
- **结构化日志落盘(ELK-ready)**：`setup_structured_logging(log_file=)` 用 RotatingFileHandler 写 JSON 行；`main.py` 按 `AUTODEV_JSON_LOGS`/`AUTODEV_LOG_FILE` 启用。
- **成本预算告警**：`set_budget(agent, amount)` + 总预算 `AUTODEV_COST_BUDGET`，超支在 `cost_tracker.alerts` 告警，`/api/cost/report` 暴露。
- **向量库 Qdrant 后端**：`QdrantVectorStore` + `make_vector_store()` 工厂（`AUTODEV_QDRANT_URL` 配置且 qdrant-client 可用→Qdrant，否则内存降级）；`VectorStore.delete` 统一删除接口。
- **延迟分位**：`get_timer_stats` 增 p50/p95/p99；仪表盘渲染 avg/p95。

### 接真批（去掉「有壳无实」）
- **工作区执行接真**：`/api/workspaces/{id}/execute` 背后的 `WorkspaceManager.execute_in_workspace`
  从原先的「模拟执行」（假进度 + `sleep`）改为真实多 Agent 流程：需求→架构→迭代开发闭环，
  与全局 `/api/start` 同构，但状态写进 workspace 对象、事件经 callback 广播，每工作区独立目录。
- **权限模型接到危险执行点**：`PermissionManager`/`SafetyGuard` 原为死代码（`check_*` 无人调用）。
  新增 `SafetyGuard.check_command()`（黑名单/危险模式 + 记录违规），在
  `/api/terminal/execute` 与 `/api/sandbox/{id}/execute` **执行前**调用，替换原弱内联黑名单；
  被拦截的命令进 `violation_history`，经 `/api/security/*` 可见。

### 工程基建 + 去重/补测批
- **版本控制**：项目纳入 git（此前无），机密经 `.gitignore` 隔离（已审计暂存区无密钥）。
- **CI**：`.github/workflows/ci.yml`，push(main)/PR 触发，矩阵 Python 3.10/3.12，
  无密钥 clean-room 跑 `pytest`（保证离线确定性）。
- **修复 `/api/docs/generate` 崩溃**：路由调用了不存在的 `analyze_python_file_content`
  （只有按路径的 `analyze_python_file`），python 分支必 500；补出按源码字符串分析的入口。
- **删假 `CoverageAnalyzer`**：永远返回 0.0 的占位、零消费者 → 移除（去负债）。
- **补测**：`src/documentation`、`src/testing` 生成器与 `PermissionManager.check_permission`
  此前零测试，补特征测试（含上面那个崩溃端点的回归守门）。
- **JS/TS AST 接真**：`JavaScriptASTParser` 从「逐行正则」升级为 **tree-sitter 真 AST**
  （装 `.[parsing]` 时启用，缺依赖回退正则）——现在能解析类方法、箭头函数、命名 import、
  TS interface/带类型参数，正则版这些全漏。顺手修了 `TestGenerator._analyze_javascript`
  缺失导致 `/api/testing/generate(js)` 崩溃的 bug（改为复用此解析器），并删除零引用的
  重复 `tree_sitter_parser.py`。tree-sitter 独有能力的测试用 `importorskip` 守门，
  CI 无依赖时跳过（138 passed, 2 skipped），本地装了则全 140。

### 收尾批（小尾巴）
- **生成器 LLM 增强**：`/api/testing/generate`、`/api/docs/generate` 改为 **LLM 优先**
  （配置 `OPENAI_API_KEY` 时 LLM 生成真测试/带解释文档，否则回退确定性版）。共享
  `src/llm` 的 `resolve_optional_client`/`strip_code_fence`。端点测试 `delenv OPENAI_API_KEY`
  保证确定性、不打网络；LLM 路径用注入 FakeLLM 测。
- **浏览器自动化补测**：`src/browser` 真实操作需 Playwright，离线测覆盖配置默认值、
  管理器查找、未启动时 fail-fast 抛 `RuntimeError` 的护栏。

### 决策/加固批
- **移除协作模块**：`src/collaboration/realtime`（多人光标/评论，Google-Docs 式）偏离
  「AI Agent 写代码」主线、零消费者、未接任何路由。按「零引用=负债」移除（git 历史可恢复），
  避免给非核心特性接线造成产品臃肿。
- **浏览器安全加固**：`/api/browser/*` 原先无闸、navigate 不校验 URL（SSRF/`file://` 风险）。
  现 **fail-closed**：默认 403，需 `AUTODEV_ENABLE_BROWSER=1`（仿 shell 闸）；navigate 经
  `validate_navigation_url` 仅放行 http/https 且拒绝环回/私有/链路本地/保留 IP
  （挡 `file://`、`127.0.0.1`、`169.254.169.254` 云元数据、内网段）。校验在启动浏览器前完成。
- **工作目录健壮性**：默认 `state.workdir` 从写死的 `~/personal_project`（多数环境不存在，
  会让 terminal/文件端点失败）改为进程当前目录 `Path.cwd()`；`/api/terminal/execute` 在
  `cwd=workdir` 前校验目录存在，不存在给明确报错而非闷头失败。
  （这是 CI 首跑只在测试层盖住、后来才真正修到产品的健壮性 bug。）

## 二、已落地（真实可用）

- **多 Agent 协作**：product / architect / developer / reviewer / tester 五个角色，
  **真实调用 LLM**（经 One API 网关，异步）。developer 会把生成代码写入工作区，
  tester 会真实运行 pytest 并诚实报告通过/失败，reviewer 给出真实裁决（可 request_changes）。
- **自我编排引擎**：任务分析 → 分解 → 能力匹配 → 执行 → 失败恢复；子任务按
  依赖链式调度，按 subtask_id 正确路由到对应 Agent，贯穿共享上下文传递产物。
- **Web 控制台**：FastAPI，`server.py` 为薄装配器，路由按域拆到 `src/web/routers/`，
  共享状态/请求模型/依赖分别在 `state.py` / `schemas.py` / `deps.py`。
- **记忆**：SQLite 会话持久化 + JSON 长期记忆。
- **技能 / Hook 系统**：可用。
- **代码编辑 / 索引**：行级编辑、diff、AST/正则解析。

## 三、部分实现 / 简化

- **语义搜索 / 向量库**：框架在位，向量检索需 `pip install '.[memory]'` 并接 Qdrant，默认未启用。
- **沙箱**：`src/sandbox`（Docker）为真隔离但需本机有 Docker；`src/cloud_sandbox`
  为**非隔离**的简化执行，默认通过开关关闭（见「安全」）。
- **任务复杂度分析**：默认基于规则（关键词/长度），对中文支持有限；可切 LLM 增强。
- **浏览器自动化**：依赖 Playwright，需单独安装。

## 四、安全（默认安全）

- **鉴权**：设置 `AUTODEV_API_TOKEN` 后，所有 `/api/*` 与 `/ws` 强制校验 Bearer/X-API-Token；
  未设则仅本地放行。鉴权逻辑见 `src/web/auth.py`。
- **执行端点 fail-closed**：`/api/terminal/execute`、云沙箱执行默认 **403**，
  需显式 `AUTODEV_ENABLE_SHELL=1` 才开启；命令再经 `SafetyGuard.check_command` 黑名单。
- **浏览器端点 fail-closed**：`/api/browser/*` 默认 **403**，需 `AUTODEV_ENABLE_BROWSER=1`；
  `navigate` 经 `validate_navigation_url` 仅放行 http/https 公网地址，挡 `file://` 与
  SSRF（环回/私有/链路本地/保留 IP，如 `169.254.169.254` 云元数据）。
- **路径围栏**：文件读取/列举/编辑统一经 `resolve_within()` 限定在工作目录内，
  拒绝绝对路径与 `..`（根治字符串前缀绕过）。
- **默认绑定** `127.0.0.1`；对外绑定且未设 token 时启动告警。

## 五、最近的重构

1. **删死代码**：移除 8 个零引用孤岛包（~3,000 行）与重复 Web 入口；`src` 31 → 23 包。
2. **拆巨石**：`server.py` 从 2112 行拆为 ~70 行装配器 + 19 个域 router（路由契约测试守门）。
3. **Agent 真实化**：五个角色从写死的占位符改为真实 LLM 驱动。
4. **安全加固**：补齐鉴权、执行闸、路径围栏，修注入。
5. **修阻塞调用**：LLM 客户端从同步改 `AsyncOpenAI`，不再冻结事件循环。

## 六、测试

```bash
python -m pytest tests/ -q     # 155 passed（无 tree-sitter 时 153 passed, 2 skipped）
```
覆盖：记忆/技能/Hook/分析器单测、真实化 Agent（注入 FakeLLM 离线）、
编排路由回归、server 路由契约 + WebSocket + 全 GET 无 500、安全（鉴权/执行闸/路径穿越）、
bug 回归（B2–B6）。

## 七、下一步

- 把任务分解也接入 LLM（当前默认规则版，中文支持弱）。
- 统一 `sandbox` 与 `cloud_sandbox`，让 Agent 执行走真隔离。
- 语义检索接入向量库。
- 持续对齐其余设计文档与实现。
