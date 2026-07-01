"""编排器（保留任务分析/分解 + 迭代 dev loop；老的 5 角色批处理引擎与 quant 已退役删除）。

历史上这里还有 SelfOrchestratingEngine / matcher / collaboration / loop_controller /
verification_loop / autonomous_loop / recovery（5 角色批处理范式）——已被交互式主 agent loop +
隔离 dev 流水线取代并删除。分析/分解（task_analyzer）与迭代 dev loop（dev_loop）仍被
/analyze、dev 流水线复用，予以保留。
"""

from .task_analyzer import TaskAnalyzer, TaskAnalysis, TaskComplexity, SubTask, TaskDecomposer
from .dev_loop import (
    IterativeDevLoop, DevLoopResult, run_iterative_development,
    AutonomousCodingResult, run_autonomous_coding,
)

__all__ = [
    "TaskAnalyzer",
    "TaskAnalysis",
    "TaskComplexity",
    "SubTask",
    "TaskDecomposer",
    "IterativeDevLoop",
    "DevLoopResult",
    "run_iterative_development",
    "AutonomousCodingResult",
    "run_autonomous_coding",
]
