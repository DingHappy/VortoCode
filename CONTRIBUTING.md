# 贡献指南

欢迎参与 VortoCode。本指南帮助你快速上手并保持代码质量。参与本项目即表示同意遵守 [行为准则](CODE_OF_CONDUCT.md)。

## 环境准备

```bash
pip install -e '.[dev]'      # 安装含开发工具的依赖
cp .env.example .env         # 配置 LLM 网关密钥（详见 README）
```

## 开发与测试

```bash
python -m pytest tests/ -q   # 跑全部测试，应全绿
ruff check src tests         # Lint
```

- **改动 Web 层 / 安全 / Agent 前后务必先跑测试。** `tests/integration/` 里有
  **路由契约安全网**（冻结 API 路由集合）、WebSocket、安全（鉴权/执行闸/路径穿越）测试——
  重构一旦丢失路由、引入 import 断裂或路径越界会立刻失败。
- 新增 API 路由时，相应更新 `tests/integration/server_routes_baseline.json` 基线。
- 修 bug 时尽量补一个回归测试（参考 `tests/unit/test_bugfix_regressions.py`）。

## 代码风格

```bash
black src tests              # 格式化（line-length 100）
ruff check src tests         # Lint
```

## 安全约定

- 切勿提交 `.env` 或任何密钥（已被 `.gitignore` 忽略）。
- 示例配置只能使用占位符或公共 API 域名，不要提交个人中转站、私有 webhook、
  内网地址或真实账号标识。
- 新增"执行命令/写文件/读文件"类能力时：命令执行走 `require_shell()` 闸，
  文件路径必须经 `resolve_within()` 限定在工作目录内（见 `src/web/auth.py`）。
- 不要在公开 PR 中依赖 repository secrets；live/联网/烧 token 测试必须默认跳过。
- 不要把来自 fork 或不可信作者的 PR 跑在带私有凭证的 self-hosted runner 上。

## 公开协作范围

- 优先欢迎 bug 修复、测试补充、文档澄清、TUI/Web 体验改进和小范围工具增强。
- 大型架构调整、权限模型变化、自动执行策略、长期记忆策略请先开 issue/discussion
  说明动机和安全边界。
- PR 尽量保持单一主题；涉及行为变化时请在描述里写清楚验证命令和剩余风险。

## 提交

- 在特性分支上开发，保持提交聚焦、信息清晰。
- 提 PR 前确保 `pytest tests/` 全绿、无新增告警。
