"""沙箱：策略（policy.py，安全内核）与执行（runner.py / docker_sandbox.py）。

刻意不在包级再导出：shell/hooks/MCP 导入 policy 时不该连带加载 Docker 沙箱。
请直接 `from src.sandbox.<模块> import ...`。
"""
