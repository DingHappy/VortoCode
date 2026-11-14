#!/usr/bin/env python3
"""VortoOne 迁移期旧命令兼容薄壳。

实际实现在 src/cli.py —— 它同时作为 console_scripts 入口（`vortocode` / `vc`）。
迁移期间保留 `python main.py ...`；日常使用 Desktop，后台服务使用内部运行入口。
"""

from src.cli import main

if __name__ == "__main__":
    main()
