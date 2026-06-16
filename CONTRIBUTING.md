# 贡献指南

欢迎参与 Auto-Dev-Crew。本指南帮助你快速上手并保持代码质量。

## 环境准备

```bash
pip install -e '.[dev]'      # 安装含开发工具的依赖
cp .env.example .env         # 配置 LLM 网关密钥（详见 README）
```

## 开发与测试

```bash
python -m pytest tests/ -q   # 跑全部测试，应全绿
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
- 新增"执行命令/写文件/读文件"类能力时：命令执行走 `require_shell()` 闸，
  文件路径必须经 `resolve_within()` 限定在工作目录内（见 `src/web/auth.py`）。

## 提交

- 在特性分支上开发，保持提交聚焦、信息清晰。
- 提 PR 前确保 `pytest tests/` 全绿、无新增告警。
