"""工具工厂包——从 main_agent.py 拆出的、**不依赖 MainAgent** 的那些。

拆分理由：main_agent.py 曾经 4300+ 行，其中工具工厂占了近一半。每加一个功能（排班、升级
通道、能力指纹…）都在同一块巨石上动刀，冲突面和阅读成本都压在一处。

**依赖方向是单向的**：`main_agent → tools.* → tools._common`。这是拆分能成立的前提——
只搬不依赖 `MainAgent` 的工厂。`build_dev_tools` / `build_research_tools` / `build_subagent`
要用 `MainAgent` 起子 agent，搬走就会成环，故仍留在 main_agent.py。

**别在 tools.* 里 import main_agent**：那会立刻把单向依赖变成环。要用 `MainAgent` 的工厂
就说明它不该在这个包里。

调用方零影响：main_agent.py 逐个再导出了这里的全部名字，
`from src.agents.main_agent import build_read_tools` 这类存量写法（src/ 与 tests/ 里几十处）
继续照常工作。
"""
