"""VortoCode: 通用多 Agent 软件开发框架。

刻意保持为空：各模块按需 `from src.xxx import ...`。此前这里预先导入 hooks/skills/向量记忆/旧工具
注册表，导致任何子模块（包括安全内核）都要先付出约 125ms 并拉进 pydantic 等无关依赖。
版本号以 pyproject.toml 为准。
"""
