"""OS 沙箱策略（policy.py，安全内核）。

代码片段执行入口 runner.py / docker_sandbox.py 只有已退役的 TUI 调用，2026-10 一并删除；
命令执行走 run_command → policy。请直接 `from src.sandbox.policy import ...`。
"""
