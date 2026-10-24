"""记忆系统。

刻意不在包级再导出：记忆写入策略（write_policy）属安全内核，导入它不该连带加载向量记忆/SQLite
会话库及其依赖。请直接 `from src.memory.<模块> import ...`。
"""
