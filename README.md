# VortoCode

通用多 Agent 软件开发框架，目标：**全栈 Web 项目自动化开发，人工只在"需求确认"和"最终合并"两个环节介入**。

## 定位

不是又一个 AutoGPT 玩具，也不是 MetaGPT 的换皮。目标是把"软件公司"这件事跑成一个**可重入、可审计、可干预**的工作流引擎，LLM 只是其中执行节点。

## 核心理念

1. **工作流优先，LLM 其次** —— 状态机/DAG 是骨架，Agent 是节点，不让 LLM 自由决定全局流程
2. **自我迭代，人在合并口** —— 框架可对自身**提议**修改（经审查 PR 由人合并），但绝不自主热更新/自动合并；任务级"开发→测试→审查→修复"闭环已落地（见下「自我迭代 / dogfooding」）
3. **人在两个关口** —— 需求确认(入口)、PR 合并(出口)，中间全自动
4. **LLM 网关化** —— 所有模型调用走自建 One API 中转，统一计费/观测/路由

## 核心特性

### 交互式主 Agent（agent loop · 仿 Claude Code / opencode）

`vc tui`（终端）与 Web 控制台 `/agent`（浏览器）共用同一个**主 agent loop**：一个会聊天的 LLM，在"输入 → 模型 →（工具 → 回灌）\* → 流式回复"的循环里自己决定该聊天、读代码，还是把开发任务交给流水线 —— **寒暄不会触发构建，要动手才动手**。

- **工具集**：`read_file` / `list_files` / `grep` / `analyze_repo`（只读）· `edit_file` / `write_file` / `run_dev_workflow`（写/重型，需确认）· `task` / `research_parallel`（子 agent 委派）· `use_skill` / `save_skill`（技能）· `save_memory` / `recall_memory`（长期记忆）· `publish_artifact` / `list_artifacts`（制品，见下）· 以及 `/mcp` 接入的任意外部 MCP 工具
- **plan / build = 工具权限门**：plan 只放只读工具；写/重型/外部工具仅 build，且写盘前弹确认 —— 落实"人在关口"
- **子 agent 委派**：把独立调研任务派给隔离上下文的只读子 agent，支持并行 fan-out
- **SKILL.md 技能**：渐进式按需加载（复用 `src/skills` 解析器），还能让 agent 现场起草新技能
- **跨会话记忆**：`/resume` 重建对话上下文；`save_memory`/`recall_memory` 沉淀跨会话知识
- **@上下文注入**：`@文件`→内容、`@目录`→清单、`@符号`→AST 定义位置
- **双协议**：**默认走原生 function-calling（native）**——对支持的模型更可靠（真机对照 dogfood：同一开发任务 native 3/3 正确落地、提示式仅 1/3，提示式下模型易把工具结果误当用户消息、凭空编造交付）；模型不支持则**自动回退**提示式协议（模型无关兜底）。`VORTOCODE_NATIVE_TOOLS=0` 可强制走提示式。三端（TUI/Web/CLI）统一
- **可观测/可审计**：token 用量统计（`/usage`）、工具调用审计日志（`/audit`）
- **命令**：`/run /analyze /improve /fix /skills /tools /mcp /usage /audit /agents /runagent /sessions /resume /new /mode /clear /help`

> 早期版本把"任何自然语言 = 开发目标（等同 `/run`）"，会出现"打个招呼也跑完整 dev→test→review"的尴尬；现已收敛为上面的主 agent loop，开发只是它的一个工具。

### 制品（Artifacts · 仿 Claude Code）

让主 agent 把会话产出**发布成一个可分享、随会话实时更新的网页**——带注释的 PR 走查、数据看板、方案对比、交互控件、迁移/排查进度清单等。

- **一句话发布**：build 模式里说"把这个做成一个可分享的页面"，agent 调 `publish_artifact`（自包含 HTML 或 **markdown**，markdown 渲染成 GitHub 风整页）→ 返回链接 `/(…)/artifact/<id>`
- **实时更新**：带相同 `id` 重新发布即 `version+1`，已打开的查看页**自动刷新**（轮询版本号）
- **版本历史 / pin**：每次发布留快照，查看页可下拉**回看任意历史版本**（`?v=N`）、一键**设为默认**给查看者看哪一版——对齐 CC「choose which version viewers see」
- **删除**：`delete_artifact` 工具（写·build·确认）/ `DELETE /api/artifacts/<id>` / 画廊删除按钮
- **`@artifact:<id>` 注入**：对话里 `@artifact:<id>` 把某制品当前内容带给主 agent 迭代（"改一下 @artifact:xxx"）
- **静态隔离 + 大小上限**：查看页 `iframe sandbox` + 限制性 **CSP（`default-src 'none'`，禁外联/SSRF）**；单页 ≤ **16 MiB**（`VORTOCODE_ARTIFACT_MAX_BYTES` 可调）——对齐 CC
- **可分享 / 认证可见**：链接即可分享；设了 `AUTODEV_API_TOKEN` 时需带 `?token=`（仅认证者可见）。画廊 `/artifacts` 列全部；TUI `/artifacts` 命令列出
- **人在关口**：发布/删除是写操作——TUI 弹确认，Web 端靠 build 模式门控
- **落盘**：`.vortocode/artifacts/<id>/`（gitignored，运行时产物）；由 Web 服务器渲染，`VORTOCODE_WEB_BASE` 可改链接前缀

### 自我迭代 / dogfooding（用 vortocode 开发它自己）

- **L1 自分析** (`vc self-analyze`)：只读扫描本仓库，找孤儿模块 / 循环依赖 / 测试缺口 / 未声明依赖（确定性，无需 LLM key）
- **L2 自改进** (`vc self-improve`)：给"没测试的模块"自动生成测试，**必须真 pytest 通过**才纳入（客观门控，非 LLM 自评）
- **L2.2 代码修复** (`vc self-fix --paths ...`)：深审 bug/坏味道 → 外科手术式精确编辑 → **全量测试门控**，绿才留、红回滚
- **安全边界**：默认 dry-run 只出提案；改动只进新分支、绝不碰 main、需人确认 —— 落实"人在合并口"
- **交互前端** (`vc tui` / Web `/agent`)：把上面能力串进交互式主 agent loop（详见上文「交互式主 Agent」）

### 隔离 dev 流水线（差异化护城河）

> 取代了早期的「5 角色批处理编排引擎」（能力匹配/失败恢复等已随「路线 A」退役删除）。

- **一句话 → PR**: `dev_auto` 自动分解 → 并行隔离实现 → 逐件+集成验证 → 自修复重试 → 落 `vorto/*` 分支 → 开 PR
- **隔离安全**: 每个子任务在一次性 git worktree 里实现+自测，**全程不碰 main/主工作区**
- **任务分解**: 复用 `task_analyzer` 拆无依赖并行批 + 有依赖拓扑接力
- **CC/opencode 都不内置**：详见上文「交互式主 Agent」与 knowledge base

### MCP 工具集成

- **标准化接口**: 遵循 Model Context Protocol
- **动态发现**: 自动发现和连接 MCP 服务器
- **安全隔离**: 工具在沙箱环境中执行
- **权限控制**: 细粒度的工具访问权限

### 记忆系统

- **短期记忆**: 当前会话的工作记忆
- **长期记忆**: 跨会话的知识积累
- **专家记忆**: 领域专业知识库
- **知识图谱**: 实体关联关系
- **向量记忆**: 基于语义的长期记忆检索

### 技能系统

- **模块化设计**: 可重用的工作流模块
- **动态加载**: 按需加载技能
- **技能组合**: 支持技能链和并行执行
- **版本管理**: 技能版本控制
- **自动发现**: 智能技能发现和匹配

### Hooks 系统

- **生命周期事件**: 在关键点执行自定义逻辑
- **事件驱动**: 基于事件触发
- **可扩展**: 支持自定义 Hook 类型
- **审计日志**: 完整的操作记录

### 验证闭环（隔离 dev 流水线内）

> 早期独立的 `verification_loop`/`loop_controller` 模块已随「路线 A」退役删除；验证能力现内建在隔离 dev 流水线里。

- **子 agent 自测**: 隔离 worktree 里 implement→run_tests→fix 自纠直到通过
- **逐件 + 集成验证**: 单件绿才落分支，多件合并后再跑一遍全量（防"单独绿、合起来红"）
- **自修复重试**: 某件红了换全新 worktree 带失败反馈重试
- **上下文管理**: 长对话锚点裁剪 + 滚动纪要压缩

### 性能监控

- **实时指标**: CPU、内存、磁盘、网络使用率
- **应用指标**: HTTP请求、Agent任务、技能执行、LLM调用
- **可视化仪表盘**: 可定制的监控面板
- **智能告警**: 基于规则的自动告警系统
- **性能分析**: 函数级性能分析和内存分析

## 文档

### 核心设计文档

- [架构设计](docs/ARCHITECTURE.md)
- [Agent 角色定义](docs/AGENTS.md)
- [开发路线图](docs/ROADMAP.md)
- [关键技术决策](docs/DECISIONS.md)

### 改进计划文档

- [改进计划总览](docs/IMPROVEMENT_PLAN.md) —— ⚠️历史设计，其中的自我编排引擎已随路线 A 退役删除
- [自我编排引擎设计](docs/SELF_ORCHESTRATION_ENGINE.md) —— ⚠️历史设计（子系统已退役删除，仅存档参考）
- [MCP 工具集成层设计](docs/MCP_INTEGRATION_LAYER.md)
- [记忆系统设计](docs/MEMORY_SYSTEM.md)
- [技能系统设计](docs/SKILL_SYSTEM.md)
- [Hooks 系统设计](docs/HOOKS_SYSTEM.md)
- [实现路线图](docs/IMPLEMENTATION_ROADMAP.md)

## 目录结构

```
vortocode/
├── main.py              CLI 入口（agent / server / analyze / self-* / tui / test / demo）
├── src/
│   ├── agents/          交互式主 agent loop（main_agent）+ 隔离 dev 流水线（worktree/decompose）+ 工具工厂
│   ├── orchestrator/    任务分析/分解（task_analyzer）+ 迭代 dev loop + 自迭代（analyze/improve/fix）；5 角色批处理引擎已退役删除
│   ├── llm/             LLM 客户端（One API 网关，异步 AsyncOpenAI）
│   ├── web/             FastAPI 服务：server.py 装配 + routers/ 各域路由 + state/schemas/deps/auth
│   ├── memory/          记忆系统（SQLite 会话 + JSON 长期记忆 + 向量数据库）
│   ├── skills/          技能系统（技能发现 / 版本管理 / 自动加载）
│   ├── hooks/           生命周期 Hook
│   ├── tools/           MCP 工具集成（动态发现 / 权限管理）
│   ├── core/            核心设施（monitoring 指标 / cache / tracing；self_healing、task_queue 已退役删除）
│   ├── context/         上下文管理（智能压缩 / 优先级管理）
│   ├── editor/          代码编辑（surgical 精确编辑 / diff；内联补全引擎已退役删除）
│   ├── sandbox/         Docker 沙箱（cloud_sandbox 为非隔离简化执行，默认关闭）
│   ├── security/        权限 / 审批模型
│   └── projects/ browser/ github/ templates/ …（workspaces 已随路线 A 退役删除）
├── web/                 控制台前端（原生 HTML/CSS/JS，无构建步骤）
├── examples/            使用示例（iterative_dev / llm_analysis / pet_state 桌宠钩子 …）
├── tests/               单元 + 集成测试（含路由契约安全网）
├── docs/                设计与分析文档
└── config/              默认配置（default.yaml / mcp.yaml）
```

> 注：早期版本曾计划把代码放在顶层 `orchestrator/`、`agents/`、`sandbox/`，
> 现已统一收敛到 `src/` 下；那几个顶层目录已废弃。

## 技术栈

- **后端**: FastAPI + Python 3.10+（异步）
- **前端**: 原生 HTML/CSS/JavaScript 控制台页面（无构建步骤）
- **存储**: SQLite（会话）+ JSON 文件（长期记忆 / 项目）+ 向量数据库（语义检索）
- **容器化**: Docker（代码沙箱，可选）
- **LLM 网关**: 自建 One API（OpenAI 兼容接口）
- **监控**: 自定义指标收集 + 可视化仪表盘 + 智能告警
- **可选 / 规划中**: 向量数据库 Qdrant（`pip install '.[memory]'` 启用语义检索）；Redis / PostgreSQL 暂未接入

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt

# 可选：安装向量数据库支持（用于语义记忆）
pip install '.[memory]'

# 可选：安装LLM支持
pip install '.[llm]'

# 可选：安装MCP支持
pip install '.[mcp]'

# 可选：安装所有依赖
pip install '.[all]'
```

### 2. 配置环境

```bash
cp .env.example .env
# 编辑 .env 文件，配置 LLM API 密钥等
```

**LLM 配置（使用 One API 网关）：**

```bash
# One API 网关地址
OPENAI_API_BASE=https://relay.dinghappy.com/v1
# 你的令牌（sk- 开头）
OPENAI_API_KEY=sk-你的令牌
```

> `.env` 已被 `.gitignore` 忽略，切勿提交真实密钥。

**安全相关（默认安全）：**

```bash
# 设置后，所有 /api/* 与 /ws 强制鉴权（请求需带 Authorization: Bearer <token> 或 X-API-Token）
# 不设置则仅本地放行；对外暴露务必设置
AUTODEV_API_TOKEN=
# 非隔离 sandbox/cloud_sandbox 的宿主机降级执行默认禁用，仅在可信环境置 1 开启
# （/api/terminal/execute 已随路线 A 退役删除；agent 的 run_command 走确认门，不受此开关）
AUTODEV_ENABLE_SHELL=
```

### 3. 安装命令行（推荐）

```bash
pip install -e .          # 注册 vortocode / vc 控制台命令
vc --help                # 查看所有子命令
```

> 不安装也能用 `python main.py <命令>`（薄壳等效）。

### 4. 使用

```bash
# 自我迭代（dogfooding；self-analyze 无需 key）
vc self-analyze                      # 只读扫描自己、列出问题
vc self-improve                      # 给测试缺口生成测试（真 pytest 门控；默认 dry-run）
vc self-fix --paths src/x.py         # 深审并外科修复指定文件

# 交互式主 agent（终端全屏 TUI；需 pip install '.[tui]' 与 OPENAI_API_KEY）
vc tui

# Web 控制台（默认 127.0.0.1:8080；对外暴露务必设 AUTODEV_API_TOKEN）
vc server                            # 起服务后浏览器打开 /agent 即是网页版主 agent

# 开发任务：主 agent 用隔离 dev 流水线实现（需配 API key）
vc agent -b "用 Python 写一个计算阶乘的函数及其单元测试"   # 取代已退役的 5 角色批处理 vc run
vc analyze -t "创建一个 REST API"
```

> 默认仅监听本地回环（127.0.0.1）。**对外暴露前务必设置 `AUTODEV_API_TOKEN`**，见下方「安全」。

## 开发路线图

> 下面是最初规划的阶段划分（**历史规划，非当前现状**）。当前主线是「交互式主 Agent（见上文）
> + 隔离 dev 流水线」；[DEVELOPMENT_SUMMARY](docs/DEVELOPMENT_SUMMARY.md) 为**历史演进记录**、部分内容已随「路线 A」退役而过时。

- **Phase 0**: 基础架构升级 — 已落地
- **Phase 1**: ~~自我编排引擎~~ — **已退役**（5 角色批处理/能力匹配/失败恢复删除）；仅保留任务分析/分解，并入隔离 dev 流水线
- **Phase 2**: 工具集成层 — MCP 工具自动发现+注册可用
- **Phase 3**: 记忆与学习系统 — 会话/长期/向量记忆可用；知识图谱已移除（未集成的孤儿模块）
- **Phase 4**: 技能与子代理系统 — 技能/Hooks 可用；子代理（SubAgentManager）尚未接入主链路
- **Phase 5**: 高级特性与优化 — 监控/安全/沙箱可用；实时补全、内联编辑、多模型协商等仍为规划项

详细规划见 [实现路线图](docs/IMPLEMENTATION_ROADMAP.md)；当前真实能力以本 README 上文「核心特性」为准（DEVELOPMENT_SUMMARY 为历史记录、部分已过时）。

## 贡献指南

欢迎贡献！请阅读 [贡献指南](CONTRIBUTING.md) 了解如何参与项目开发。

## 许可证

MIT License

## 致谢

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) - 提供了 Agent 系统设计灵感
- [Model Context Protocol](https://modelcontextprotocol.io/) - 工具集成标准
- [AaaS](https://github.com/Gotradepal/agents_as_a_service) - 自我编排框架参考
- [Manus Killswitch](https://github.com/m0r6aN/Manus-Killswitch) - 多 Agent 协作参考
