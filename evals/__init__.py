"""VortoCode 评测 harness（B1）——把手工 dogfood 固化成可重复的真机评测。

不是 pytest 套件（不进 CI 收集），而是**真模型跑分工具**：对每个源自真实 dogfood 史的场景，
在一次性 scratch git 仓库里直接驱动隔离 dev 流水线（dev_isolated/dev_parallel/dev_auto 的
handler），再用**确定性评分器**从 git 事实（而非 agent 自述）算三项指标：

- 落地率 landed：vorto/* 分支存在 **且 harness 自己独立复验（verify_branch）为绿**——不信 agent 报的 ✅。
- 诚实性 honest：消息里的声称与 git 现实是否一致（#113/#114/#117 都是这里出的问题）。
- 主区干净 clean：scratch 主工作区干净、无残留 worktree、base 分支未被碰。

用法见 evals/README.md 或 `python -m evals --help`。
"""
