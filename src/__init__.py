"""VortoOne: 通用多 Agent 软件开发框架。

刻意保持精简：各模块按需 `from src.xxx import ...`。此前这里预先导入 hooks/skills/向量记忆/旧工具
注册表，导致任何子模块（包括安全内核）都要先付出约 125ms 并拉进 pydantic 等无关依赖。
版本号以 pyproject.toml 为准。

唯一的例外是下面这一行：环境变量从 `VORTOCODE_*` 改名为 `VORTOONE_*`（ADR-018 批次 B），
任何入口（vc、uvicorn、脚本、测试）只要导入了 src，就先把旧名字换成新名字。只用标准库，几乎不花时间。
"""
from src.env_compat import adopt_legacy_env as _adopt_legacy_env

_adopt_legacy_env()
