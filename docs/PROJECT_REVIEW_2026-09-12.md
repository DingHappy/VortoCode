# VortoCode 项目检查与方向建议 · 2026-09-12

检查基线：本地 `main @ b334682`（提交日期 2026-09-11）。检查开始时工作区干净。本报告基于该基线源码、离线测试、临时目录中的最小复现，以及当日查阅的官方资料；没有做同题竞品性能排名，也没有验证线上部署或安装包。用户随后同意按建议推进，第一批四项修复及后续费用、预算计量、Agent 评测与浏览器步骤验收已在工作区实现，实施与验证记录见文末；下文缺陷描述和源码行号保留检查时的基线口径。

## 建议定位

建议聚焦：**面向独立开发者与小团队，本地优先、成本可控、结果可追溯的工作交付台。以已有项目的开发维护为核心，运营与值守作为可选工作流。**

用户要能清楚知道：目标是什么、现在做到哪、需要自己决定什么、结果是否经过验证、重启后从哪里继续、已经花了多少。

当前工程基础已经覆盖主 Agent、隔离 worktree、持久 DevPlan、Goal 验收、PR 审查、Desktop/Gateway、Skills/MCP、IM、cron，以及新增加的非代码 Pipeline/Product。下一阶段的收益主要来自把这些能力接成可靠的完整流程。

用户已同意按本建议分批推进，从执行可靠性修复继续推进至交付验收与预算计量。内容运营、渠道数据和早报功能继续作为同一 runtime 上的领域模块，避免每个场景继续向核心和首页增加一套概念。

## 主流方向与本项目的对应关系

以下是官方已文档化的能力；对 VortoCode 的取舍属于本报告的推断。

| 方向 | 官方证据 | 对 VortoCode 的启示 |
| --- | --- | --- |
| 并行、后台执行与交接 | Codex 支持隔离 worktree、后台任务与 Local/Worktree 交接；当前官方工作台文档也明确本地定时任务依赖电脑和应用持续运行。[Worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)、[Scheduled tasks](https://learn.chatgpt.com/docs/automations?surface=app) | worktree 和后台任务已经是对标能力。重点应转向恢复、跨入口一致性和可审查结果。Desktop 管理的 runtime 退出即停止，需明确区分本地常驻与远程持续运行。 |
| Agent 实际使用自己修改的软件 | Cursor Cloud Agents 使用隔离 VM，可运行应用并提供截图、视频和日志。[Cloud Agents](https://cursor.com/docs/cloud-agent)、[官方发布说明](https://cursor.com/changelog/02-24-26) | 在现有浏览器探测上增加真实用户流程和断言，交付证据要能展示功能是否成立。 |
| 工作嵌入现有开发流程 | GitHub Copilot cloud agent 支持调研、计划、分支修改、测试以及 PR 迭代，并支持自动化触发。[GitHub 官方文档](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent) | 围绕 issue/需求到可审查 PR 优化体验和指标，避免只统计调用次数、角色数量或生成代码量。 |
| 并行方式按任务选择，过程可恢复 | Claude Code 区分子 Agent、后台会话、团队和 worktree；Agent Teams 仍标记为实验功能。Checkpoint 可恢复其编辑工具追踪的改动，但不涵盖所有 shell 或外部修改。[并行方式](https://code.claude.com/docs/en/agents)、[Agent Teams](https://code.claude.com/docs/en/agent-teams)、[Checkpointing](https://code.claude.com/docs/en/checkpointing) | 保留简单任务的短路径；仅对有独立边界的工作并行。恢复能力必须说清覆盖范围，不能把保存聊天记录等同于恢复执行。 |
| 持久状态与开放技能格式 | LangGraph 把 checkpoint、人工中断和失败恢复作为基础能力；Agent Skills 明确元数据、正文和参考资源的渐进加载。[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Agent Skills 规范](https://agentskills.io/specification) | 借鉴状态合同与兼容规范，继续复用已有 runtime；无需因为这些方向重新迁移整个框架。 |

因此，项目应把优势表述为“完整交付中的可靠性、证据和本地控制”，并通过任务实测证明。README 中把若干能力描述为竞品不具备的说法需要重新逐项核实。

## 当前值得保留的基础

- `src/agents/review.py` 已拒绝把无效审查输出当作无问题，审查错误会阻止放行。
- `src/gateway/goals.py` / `runs.py` 已把验收证据绑定 Git revision，并在代码变化后判定旧证据失效。
- `src/gateway/pipeline.py` / `products.py` 已实现非代码工序、产出物持久化、血缘、人工驳回与重做。
- `signals.py` / `channel_stats.py` 把确定性采集与模型解读分开。这比让模型猜测业务数据更适合持续运行。
- `desktop/README.md` 已有受管 Python、sidecar、bundle smoke 和发布证据流程。本次没有重新构建或验证这些产物，不能由脚本存在推断公开分发已完成。

## 第一批：优先修复四个已复现的执行合同缺口

以下四项均在临时目录、无网络、无真实 LLM、无真实发布动作的条件下复现。前三项调用基线生产函数；第四项注入持久化失败返回值。它们是检查时的实现缺口，不是线上事故结论；现已修复并加入回归覆盖。

### 1. 审批没有绑定用户实际审阅的工序与产出版本

位置：`src/web/routers/pipelines.py:103`、`web/review.html:157`、`src/gateway/pipeline.py:505`。

Web 请求只携带 `run_id + verdict + comment`，后端每次重新寻找“当前待审批工序”。

复现：页面 A 看的是 scout；入口 B 批准 scout 并推进到 write；页面 A 再提交旧的批准请求。结果返回 **`write 已放行`**，write 的状态变成 `done`，但页面 A 没有审阅 write。

修改建议：

- 请求绑定 `stage_id`、`product_id` 与单调递增的状态 revision；必要时附内容指纹。
- 后端原子校验当前状态与请求一致，不一致返回 409，要求刷新后重新审阅。
- 同一审批请求的重复提交不能推进到下一道工序；同一工序重做后的新产出不能复用旧批准。
- Web、IM、CLI 共用内核规则，不能只在网页按钮上防连点。
- 批准、拒绝、延期均持久记录审阅目标、操作者、版本和结果。

验收：两窗口、跨入口、重做换版、网络重试下，旧请求全部不能批准新目标。

### 2. 多入口同时推进会重复执行同一工序

位置：`src/gateway/pipeline.py:350`、`:367`、`:414`；调用方包括 `pipeline_tick.py`、`pipeline_cli.py` 和 `src/im/bridge.py`。

`running` 被视为可以重试的状态，但没有区分“原执行者已经崩溃”和“原执行者仍在工作”。在第一个执行器 await 时，第二次 `advance()` 会再次执行该工序。

复现结果：同一 run 的执行器调用 **2 次**，创建 **2 份 Product**；最终台账却只有 **1 次 attempts、10 tokens**，而两个执行器各报告 10 tokens。

修改建议：

- 按仓库与 run 建立跨进程的原子领取机制；单纯 `asyncio.Lock` 无法覆盖独立 CLI 进程。
- 执行记录包含 `attempt_id`、owner、租约/心跳与状态 revision，恢复前确认旧 owner 不再持有执行权。
- 对有外部副作用的步骤采用稳定操作 ID、回执和对账；不能宣称一个锁就能保证任意外部系统的 exactly-once。
- 对不同 run 保留并行能力，同一 run 的重复触发返回明确的 busy/已有执行信息。

验收：同进程并发与跨进程并发分别验证；同一工序只执行一次，用量和产物不丢失；崩溃后仍可恢复。

### 3. 输入回退会消费同仓库另一条流水线的数据

位置：`src/gateway/pipeline.py:338`、`src/gateway/products.py:186`。

注释规定“本次运行 → 同流水线 → 不属于任何流水线的公共产物”，实际最后调用 `store.latest(kind)`。底层空字符串表示“不筛选”，因此能选到另一条流水线的产物。

复现：仓库只有 pipeline-B 的 metrics，启动 pipeline-A 并解析 metrics 输入，A 得到了 B 的数据。

修改建议：

- 显式区分“不筛选”和“只筛选公共产物”，例如独立 `latest_global()` 或类型化 scope。
- 同流水线回流优先引用经过认可的产出，定义哪些输入可缺省、哪些缺失就阻塞。
- 对有时效的数据记录采集时间、来源、有效期与所属范围，缺失/过期不能伪装成零值或最新数据。

验收：A/B 同名 kind 不串用；明确公共 signals 可共享；跨轮回流、缺失和过期按合同处理。

### 4. 保存 checkpoint 失败后仍执行并报告完成

位置：`src/gateway/pipeline.py:418`、`:449`，以及 review/settle 中的同类保存路径。

`PipelineStore.save()` 返回布尔值，但推进过程没有检查返回结果。

故障注入：在正常创建 run 后，让 `save()` 返回 False。执行器仍调用 **1 次**，接口结果为 **`done`**，持久状态仍为 **`pending`**。本次没有制造真实磁盘损坏。

修改建议：

- 开始执行前，持久化失败必须阻止执行器调用。
- 执行后保存失败应报告“执行结果待对账”，保留 attempt 与回执，避免直接重做外部动作。
- 原子替换文件只能保护单次写入完整性，不能替代整个读改写事务或并发比较更新。
- 对批准和拒绝的持久化失败同样如实返回，不能让界面显示成功。

验收：开始前、产物生成后、审批落盘时分别注入失败；不能出现“接口完成、磁盘仍 pending”。

## 第二批：建立衡量真实结果的评测与验收

### 扩展现有评测层级

`evals/runner.py:81` 直接调用 dev tool handler。仓库提交的基线为 2026-07-04、7 个场景各 3 次、共 21 次；其中一份落地率 93.3%、总通过率 95.2%。这些是历史、工具层、小样本成绩，不能代表当前完整产品的成功率。

项目并非完全没有端到端测试：`tests/e2e/test_delivery_chain.py` 覆盖交付接缝，使用 ScriptedLLM/FakeChannel；`tests/live/test_live_agent_flow.py` 有需要显式启用的真模型测试。应在这些基础上增加真实任务回归集。

建议先选 10–20 个明确可验收的维护任务，覆盖 Python/TypeScript 修复、跨文件功能、失败测试修复、半途恢复、审批换版和多流水线输入。每项保留基准 commit、模型与配置、输入、运行轨迹、独立验收结果、总耗时、tokens 和人工介入次数。

核心指标采用：真实任务验收率、误报完成率、恢复成功率、人工介入次数、每个验收通过任务的成本。离线回归和需要真实模型的评测分开运行；候选版本使用固定任务集进行比较。

### 把浏览器探测升级为用户流程验收

`src/browser/verify.py:137` 当前主要完成页面加载、错误检测、网络边界检查和截图，是有效的页面健康探测。它尚不能仅凭成功结果证明“登录、创建项目、保存数据”等业务流程成立。

建议增加声明式步骤、可访问性定位、明确断言、失败截图与 Playwright trace；把证据关联 Goal criterion、run、被验收的 commit 和环境。先覆盖两个真实流程，再考虑更通用的 computer-use 能力。

验收示例：提交表单 → 出现新记录 → 刷新页面 → 记录仍在；失败时能定位具体步骤。不要把截图存在直接等价为业务验收通过。

### 把用量记录补成准确的预算控制

已有 `usage_scope()`、cron 的 `BudgetedLLM` 和 Pipeline 的 tokens 记录，应继续复用。当前边界包括：

- `src/models/cost.py:12` 对未配置价格的模型返回 0；未知价格应展示为未知，并保留 tokens。
- `src/llm/budget.py` 当前以进程累计量的增量估算预算，文档也承认同进程并发任务会互相计入，应逐步改为任务级归因。
- `src/agents/pipeline_exec.py:122` 的步数上限与完成后 token 统计不能替代每道工序和整轮的 token/时间预算。
- `ModelRouter` 当前没有作为主 Agent 的自动选模决策器使用；其静态质量/速度评分不应被当成生产实测。

建议保留 Relay 默认入口，增加真实能力探测和可配置价格。先用评测找出哪些任务值得使用更强模型，再开放有证据的角色配置；不根据模型名或静态分数自动升级成本。

## 第三批：统一产品入口，逐步拆分模块

### 让用户围绕工作与结果组织操作

README 定义 Desktop 为默认入口，但新 Pipeline 审批目前落在 `web/review.html` 与 `/api/pipelines`。本次检索 Desktop、Inbox 和决策投影代码，没有发现对应 Pipeline 接入。

建议 Desktop 增加 Pipeline 待办投影和详情入口，与已有 Goal、Task、Run 共用导航与决策队列。界面围绕“目标、进度、待决定、结果、费用”展示，详情再保留底层对象的独立语义；不用强行把几个状态机合成一个对象。

代码交付和运营流水线可共享产出引用、审批与审计能力，但具有不同验收标准：代码看测试与 commit，运营看来源、稿件、真实投递回执和采集数据。

### 收口当前路线与实现

- README 顶部的“人工只在需求确认和最终合并介入”与已有权限确认、污点提示、工序审批并不完全一致。应说明授权边界内自动推进、在需要决定时介入。
- 历史五角色阶段规划继续归档，当前路线图只保留近期里程碑与可验证的完成条件。
- 明确开发交付、运营模块和部署边界，避免把已有脚本、设计草案与完成验收混在一个能力列表。

### 渐进拆分高耦合入口

本次文件行数：`desktop/src/App.tsx` 4,074 行，`desktop/src-tauri/src/lib.rs` 4,479 行，`src/agents/main_agent.py` 1,563 行，`agent_loop.py` 1,776 行。

行数本身不证明性能或正确性问题，但新增工作流会进一步增加入口文件的维护压力。建议在修改相关功能时逐步提取 runtime 管理、会话、Goal、审查、Pipeline、模型设置等边界，先保留对外协议和既有行为。近期 `src/utils` 的边界规则下沉可以继续，不需要一轮大重写。

## 建议的实施顺序

| 批次 | 交付 | 完成条件 |
| --- | --- | --- |
| 1 | 审批目标绑定；执行领取与保存失败处理；输入 scope 修复 | 本报告四类复现转成回归测试；跨入口行为一致；原相关测试通过 |
| 2 | 真实任务集、两条浏览器业务流程、任务级预算与未知价格展示 | 固定 commit 上可重复出具验收报告；成功/失败/未验证准确区分 |
| 3 | Pipeline 进入统一待办；README/当前路线收口；沿功能逐步拆模块 | 用户从默认入口可找到待审批工作、完整产出与执行结果 |
| 4 | 根据真实使用情况扩展远程 runner、领域工作流与执行器适配 | 每项扩展有实际用户任务、成本记录和恢复验收，不以功能数量作为目标 |

建议近期推迟单独重做 IDE、扩大角色组织架构、泛化工作流编辑器和大规模重写。优先验证开发维护这一核心入口，再通过实际使用决定运营模块是否值得成为第二条产品主线。

## 检查阶段验证记录（修复前）

```bash
OPENAI_API_KEY='' VORTOCODE_API_TOKEN='' \
VORTOCODE_ENABLE_SHELL='' VORTOCODE_ENABLE_BROWSER='' \
PYTHONDONTWRITEBYTECODE=1 python -m pytest \
  tests/unit/test_pipeline.py tests/unit/test_pipeline_tick.py \
  tests/unit/test_review_console_api.py tests/unit/test_products.py \
  tests/unit/test_goal_revision.py tests/unit/test_review.py \
  tests/unit/test_evals.py -q -p no:cacheprovider
```

结果：**151 passed，10.43 秒，1 条 FastAPI TestClient/httpx 弃用警告**。这是所列范围的当前验证，不是全仓测试结果。

另外使用独立的 Python 最小复现，在 `TemporaryDirectory` 中调用 `advance()`、`review()`、`resolve_inputs()`，以 `asyncio.Event` 控制并发时序，以 `unittest.mock.patch` 注入 `PipelineStore.save=False`。观察值如下：

```json
{
  "concurrent_advance": {
    "executor_calls": 2,
    "products": 2,
    "recorded_attempts": 1,
    "recorded_tokens": 10,
    "returned_statuses": ["done", "done"]
  },
  "stale_approval": {
    "tab_A_viewed": "scout",
    "currently_waiting": "write",
    "stale_request_result": "write 已放行",
    "write_status": "done"
  },
  "cross_pipeline_input": {
    "consumer": "pipeline-A",
    "input_pipelines": ["pipeline-B"]
  },
  "checkpoint_write_failure": {
    "executor_calls": 1,
    "returned_status": "done",
    "persisted_stage_status": "pending"
  }
}
```

检查阶段仅新增报告，此时尚未修改生产代码。之后用户同意按建议推进，实施结果如下。

## 第一批实施记录

实施基线仍为 `b334682`，改动保留在工作区，未提交、推送或部署。

| 缺口 | 已实现行为 | 主要回归证据 |
| --- | --- | --- |
| 旧审批可能放行下一道工序 | 审批绑定运行 revision、待审工序、产出 id 与完整产出内容；批准、驳回、挂起都校验版本，并保存审批记录 | 缺少凭据、跨工序旧凭据、驳回后旧凭据、正文被改写均拒绝；Web、CLI、IM 接入同一合同 |
| 同一运行被并发执行 | OS 文件锁覆盖读取、执行和保存全过程；不同运行可独立推进；进程退出后锁自动释放 | 同进程并发只调用一次执行器、生成一个产出；真实子进程执行期间另一进程被拒绝，终止旧进程后可恢复 |
| 输入串入其他流水线 | 公共产出回退显式要求没有 pipeline 归属；保留本轮、同流水线、公共范围的查找顺序 | A 无输入时不会读取 B；存在公共输入时只回退公共输入 |
| 保存失败仍继续或报成功 | 执行前必须保存 attempt；保存失败返回错误；产出回执使用预留 id；状态保存增加版本比较，防止旧快照覆盖 | 执行前保存失败时执行器零调用；产出已保存但结束状态失败时恢复回执，执行次数和 token 不重复计入；审批保存失败不报成功 |

状态与产出写入采用同目录独立临时文件、文件 fsync、原子替换及 POSIX 目录 fsync。运行锁文件保留，避免删除后不同进程锁住不同 inode。定时通知发送后重新读取状态，避免在等待通知期间发生的审批被旧快照覆盖。

### 审批入口的使用变化

- Web 详情接口返回 `review_token`，提交审批时原样带回。缺少凭据、版本过期或运行忙时返回 HTTP 409；状态保存失败返回 HTTP 503。页面遇到 409 刷新内容，用户重新审阅后再次操作，不自动补发批准。
- CLI 先执行 `vc pipeline show <run_id>`，查看完整 JSON 产出及页面打印的审批命令。`vc pipeline review <run_id> --review-token <token> --approve` 中的凭据也适用于 `--reject` / `--defer`；旧的无凭据调用会被拒绝。
- IM 先 `/pipe <run_id>` 查看完整产出，再使用 `/ok`、`/no <意见>` 或 `/later`。仅成功发送完整预览才保留该版本凭据，处理后消费；重启 IM 后需要重新查看。超过预览上限的内容引导至审批台，截断摘要不能直接批准。
- Web、CLI、IM 均以同一份用于展示的产出快照生成凭据，避免展示后重新读取另一版本再授权。

### 恢复边界

已落盘且匹配本次 attempt 的产出回执可用于恢复完成状态，恢复时不再调用执行器。中断的只读工序可在次数上限内重试。

对外工序若可能已执行、却缺少完整回执，或执行器抛出无法确定外部结果的异常，则进入 `needs_reconciliation`（待核对执行结果），后续推进不会自动重发。能力或授权预检明确尚未开始执行的失败仍可重试。旧版本中断记录也按此边界保守恢复。

这批修复没有提供外部服务的幂等协议或自动核对接口；待核对状态仍需人工检查真实外部结果。输入的新鲜度、是否通过审批才可回流、任务级预算、真实浏览器业务验收和 Desktop 待办整合仍属于后续批次。

### 实施验证

第一批最终相关测试：**194 passed，2.40 秒，1 条现有 FastAPI TestClient/httpx 弃用警告**。

```bash
OPENAI_API_KEY='' VORTOCODE_API_TOKEN='' \
VORTOCODE_ENABLE_SHELL='' VORTOCODE_ENABLE_BROWSER='' \
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
python -m pytest \
  tests/unit/test_pipeline.py tests/unit/test_pipeline_tick.py \
  tests/unit/test_pipeline_cli.py tests/unit/test_review_console_api.py \
  tests/unit/test_im_pipeline_review.py tests/unit/test_products.py \
  tests/unit/test_pipeline_exec.py tests/unit/test_pipeline_contracts.py \
  tests/unit/test_outbound_is_real.py tests/unit/test_session_pipeline_boundary.py \
  tests/unit/test_review_console_behavior.py tests/unit/test_product_preview.py \
  -q --tb=short
```

新增审批页测试在 Node VM 中执行实际页面脚本，覆盖请求期间切换任务、版本冲突刷新及不自动再次批准；这是脚本行为测试，不是浏览器 GUI 或业务验收。

实施期间另运行了 `tests/unit tests/test_basic.py tests/integration tests/live tests/e2e`：**2743 passed，17 failed，10 skipped，169.29 秒**。17 条失败均为当前环境禁止绑定 `127.0.0.1` 监听端口，分布在 `test_gateway_client.py`（10 条）、`test_llm_list_models.py`（4 条）与 `test_tui_attach.py`（3 条）。将未修改的 `HEAD` 解包至独立临时目录后复跑同一组 17 条测试，全部以相同端口权限错误失败。因此全量测试不能标为通过；最终预览与恢复补充改动由上述 194 条专项测试覆盖。

全仓 `python -m ruff check src tests`、项目配置范围内的 `python -m mypy`（8 个源文件）及 `git diff --check` 通过。额外对本次涉及的 8 个核心/入口文件显式运行 mypy，剩余 5 条圈外既有错误；在未修改 HEAD 的对应文件中复现相同错误，没有为此放宽类型规则。

第一批结束时没有运行付费模型、真实 IM 投递、桌面安装包或浏览器业务流程验收；跨进程锁已在本机 POSIX 环境验证，Windows 分支尚未实机验证。后续浏览器业务验收结果见下节。

## 后续实施：费用、任务计量与交付验收

### 已落地的行为

- **费用如实展示**：未配置单价的模型返回 `None`，API 的 `total_cost` 在有未计价调用时返回 `null`，同时提供 `known_cost`、`pricing_complete`、`unpriced_calls`、`unpriced_tokens`。TUI 展示未知费用及已知小计。明确配置的零单价仍表示免费，不能与未配置混淆。单价沿用项目目录估算，未在本轮校准供应商账单。
- **任务预算独立计量**：新增 ContextVar 任务计数器，与既有会话/阶段计量并存；嵌套阶段、子任务和流式用量进入本任务预算，不受外部并发任务、会话清零影响。同一 BudgetedLLM 的请求串行检查预算，避免并发请求同时通过剩余额度检查。cron 覆盖完整任务作用域；单次超额即使没有下一次请求也能被记录为超限。
- **真实 Agent 执行入口**：`python -m evals --via-agent` 通过 MainAgent 对话及工具选择完成任务，沿用真实隔离开发工具。新增 Python 分页、跨文件配置污染和 TypeScript 空集合修复，共 10 个场景；三个新场景有 harness 管理的独立业务断言。离线测试证明删除红测试不能使错误实现通过。
- **评测证据与退出状态**：保存基准与交付 commit、输入、完整回复、工具轨迹、逐模型用量、费用完整性、自动确认及独立验收输出。相同 fixture 生成稳定基准 commit。Agent 基线与工具层基线隔离；失败、跳过、零结果或执行异常不能报绿，包含不要求产出改动的负向场景。
- **浏览器步骤验收**：`browser.steps` 支持基于角色、标签等定位的操作和明确断言，失败时停止后续步骤，保存截图与 trace。原页面健康探测与用户流程验收分别标记，不能由截图存在推断业务通过。运行时证据包含 run、profile、源码 commit、dirty 与环境；验收期间源码状态改变会报失败。

使用方式见 [评测说明](../evals/README.md) 和 [浏览器用户流程验收](BROWSER_WORKFLOW_VERIFICATION.md)。成本 API 的 nullable 总费用和 CLI/IM 审批版本凭据是调用方需要适配的合同变化。

### 当前验证结果

| 检查 | 结果 | 验证边界 |
| --- | --- | --- |
| 全量 Python 离线回归 | **2799 passed，11 skipped，184.70 秒** | 本次允许测试监听本机端口，因此第一批遇到的 17 条环境失败均通过；真实模型/浏览器仍按测试开关跳过 |
| 两批改动合并后的专项回归 | **375 passed，24.86 秒** | 覆盖审批、恢复、输入范围、费用、预算、评测与浏览器配置；包含全量运行后的补充改动 |
| 最终 Agent 异常处理补测 | **13 passed，3.07 秒** | 补充验证真实 MainAgent 的模型异常不能让负向场景报绿 |
| 真实浏览器验收 | **5 passed，10.32 秒** | 两条审批流程、一次故意失败的步骤、两个已有页面探测 |
| 静态检查 | Ruff、项目配置范围 mypy、`git diff --check` 通过 | 额外检查浏览器、预算、LLM 与费用 5 个源文件的 mypy 也通过；第一批圈外既有类型欠账未扩大处理 |

浏览器使用 Playwright 1.62.0 与本机已安装 Chrome，独立临时用户目录。因为 Chromium 下载超时，通过临时 pytest 启动插件指定 `channel=chrome`，没有修改生产默认 Chromium 选择。临时测试服务使用真实审批 HTML、生产路由与持久存储；产出由确定性执行器准备，不调用 LLM，不进行 IM 投递。

两条流程的实际证据：

- [批准后刷新截图](../.vortocode/artifacts/project-review-2026-09-12-rq1nf4xj/approve.png)、[逐步结果](../.vortocode/artifacts/project-review-2026-09-12-rq1nf4xj/approve.json)、[trace](../.vortocode/artifacts/project-review-2026-09-12-rq1nf4xj/approve.trace.zip)。
- [驳回后刷新截图](../.vortocode/artifacts/project-review-2026-09-12-rq1nf4xj/reject.png)、[逐步结果](../.vortocode/artifacts/project-review-2026-09-12-rq1nf4xj/reject.json)、[trace](../.vortocode/artifacts/project-review-2026-09-12-rq1nf4xj/reject.trace.zip)。

证据保存在本地 gitignored 的 `.vortocode/artifacts/`，不随源码提交。所有生产改动仍在 `b334682` 之上的未提交工作区，没有推送或部署。

### 仍需后续推进

这轮没有运行真实付费模型的 10 项任务基线，离线结果不能当作模型成功率。任务预算目前是调用之间的闸门，已发出的单次调用仍可能超额；Pipeline 整轮/每工序持久 token 与时间预算、可配置价格和供应商账单校准仍需后续实现。输入新鲜度与认可条件、Goal criterion 的浏览器流程自动绑定、Desktop 统一待办也尚未完成。
