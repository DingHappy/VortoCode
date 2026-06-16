# Auto-Dev-Crew

通用多 Agent 软件开发框架，目标：**全栈 Web 项目自动化开发，人工只在"需求确认"和"最终合并"两个环节介入**。

## 定位

不是又一个 AutoGPT 玩具，也不是 MetaGPT 的换皮。目标是把"软件公司"这件事跑成一个**可重入、可审计、可干预**的工作流引擎，LLM 只是其中执行节点。

## 核心理念

1. **工作流优先，LLM 其次** —— 状态机/DAG 是骨架，Agent 是节点，不让 LLM 自由决定全局流程
2. **任务级自我迭代，而非框架级自我进化** —— 第一版只闭环"开发→审核→修改→测试"，框架自身不改自己代码
3. **人在两个关口** —— 需求确认(入口)、PR 合并(出口)，中间全自动
4. **LLM 网关化** —— 所有模型调用走自建 One API 中转，统一计费/观测/路由

## 核心特性

### 自我编排引擎

- **动态任务分解**: 根据任务复杂度自动拆分子任务
- **能力匹配**: 根据 Agent 能力和历史表现动态分配任务
- **并行优化**: 自动识别可并行执行的任务
- **失败恢复**: 智能重试、回滚和升级机制

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

- [改进计划总览](docs/IMPROVEMENT_PLAN.md)
- [自我编排引擎设计](docs/SELF_ORCHESTRATION_ENGINE.md)
- [MCP 工具集成层设计](docs/MCP_INTEGRATION_LAYER.md)
- [记忆系统设计](docs/MEMORY_SYSTEM.md)
- [技能系统设计](docs/SKILL_SYSTEM.md)
- [Hooks 系统设计](docs/HOOKS_SYSTEM.md)
- [实现路线图](docs/IMPLEMENTATION_ROADMAP.md)

## 目录结构

```
auto-dev-crew/
├── main.py              CLI 入口（run / server / analyze / test / demo）
├── src/
│   ├── agents/          角色化 Agent（product/architect/developer/reviewer/tester，真实 LLM 驱动）
│   ├── orchestrator/    自我编排引擎（任务分析 / 分解 / 能力匹配 / 失败恢复）
│   ├── llm/             LLM 客户端（One API 网关，异步 AsyncOpenAI）
│   ├── web/             FastAPI 服务：server.py 装配 + routers/ 各域路由 + state/schemas/deps/auth
│   ├── memory/          记忆系统（SQLite 会话 + JSON 长期记忆 + 向量数据库）
│   ├── skills/          技能系统（技能发现 / 版本管理 / 自动加载）
│   ├── hooks/           生命周期 Hook
│   ├── tools/           MCP 工具集成（动态发现 / 权限管理）
│   ├── monitoring/      性能监控（指标收集 / 仪表盘 / 告警 / 性能分析）
│   ├── editor/          代码编辑与索引
│   ├── sandbox/         Docker 沙箱（cloud_sandbox 为非隔离简化执行，默认关闭）
│   ├── security/        权限 / 审批模型
│   └── context/ projects/ workspaces/ browser/ github/ templates/ …
├── web/                 控制台前端（原生 HTML/CSS/JS，无构建步骤）
├── examples/            使用示例（basic_usage / llm_analysis / real_pipeline …）
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
# 宿主机命令执行端点（/api/terminal/execute 等）默认禁用，仅在可信环境置 1 开启
AUTODEV_ENABLE_SHELL=
```

### 3. 启动服务

```bash
# 启动后端（默认 127.0.0.1:8080）
python main.py server

# 或用 uvicorn
uvicorn src.web.server:app --reload
```

> 默认仅监听本地回环（127.0.0.1）。**对外暴露前务必设置 `AUTODEV_API_TOKEN`**，见下方「安全」。

### 4. 运行示例

```bash
# 基本使用示例
python examples/basic_usage.py

# LLM 增强分析示例
python examples/llm_analysis.py

# 端到端真实流水线（产品→架构→开发→审查→测试，需配置 API key）
python examples/real_pipeline.py "用 Python 写一个计算阶乘的函数及其单元测试"

# 分析任务
python main.py analyze --task "创建一个 REST API"

# 运行任务
python main.py run --task "实现用户认证功能"
```

## 开发路线图

- ✅ **Phase 0**: 基础架构升级 (1-2 周) - 已完成
- ✅ **Phase 1**: 自我编排引擎 (2-3 周) - 已完成
- ✅ **Phase 2**: 工具集成层 (2-3 周) - 已完成
- ✅ **Phase 3**: 记忆与学习系统 (2-3 周) - 已完成
- ✅ **Phase 4**: 技能与子代理系统 (2-3 周) - 已完成
- ✅ **Phase 5**: 高级特性与优化 (3-4 周) - 已完成

详细路线图请参考 [实现路线图](docs/IMPLEMENTATION_ROADMAP.md)

## 贡献指南

欢迎贡献！请阅读 [贡献指南](CONTRIBUTING.md) 了解如何参与项目开发。

## 许可证

MIT License

## 致谢

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) - 提供了 Agent 系统设计灵感
- [Model Context Protocol](https://modelcontextprotocol.io/) - 工具集成标准
- [AaaS](https://github.com/Gotradepal/agents_as_a_service) - 自我编排框架参考
- [Manus Killswitch](https://github.com/m0r6aN/Manus-Killswitch) - 多 Agent 协作参考
