#!/usr/bin/env python3
"""VortoCode 命令行入口（薄壳）。

实际实现在 src/cli.py —— 它同时作为 console_scripts 入口（`vortocode` / `vc`）。
保留本文件是为了 `python main.py ...` 这种本地直跑方式仍然可用。
"""

from src.cli import main

if __name__ == "__main__":
    main()
