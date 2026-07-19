"""VortoCode gateway：常驻后台的任务运行时（D1 起步）。

先落地"后台任务 + write-ahead 台账 + 崩溃可恢复"这一层（tasks.py）：长流水线不再占住一个交互回合
十几分钟，而是提交即返回、后台跑、随时查询/取消、进程崩溃后可恢复（running→interrupted，接 C1 的
dev_resume 续跑）。cron/heartbeat（D3）与单核多前端（B2 长期项）在其上迭代。
"""

from .tasks import BackgroundTask, TaskLedger, TaskRunner, bg_concurrency
from .goals import (
    AcceptanceCriterion,
    CriterionVerifier,
    Goal,
    GoalEvidence,
    GoalLedger,
    evaluate_file_verifier,
)
from .runs import CommandRun, RunLedger, RunManager
from .terminals import TerminalManager, TerminalSnapshot
from .review_threads import ReviewConflict, ReviewThreadStore
from .git_review import (
    apply_review_action,
    commit_reviewed,
    open_reviewed_pr,
    review_diff,
    review_snapshot,
)
from .worktree_sessions import worktree_workspace_snapshot
from .pr_delivery import current_failed_check_log, current_pr_delivery

__all__ = [
    "AcceptanceCriterion", "BackgroundTask", "CommandRun", "CriterionVerifier", "Goal", "GoalEvidence", "GoalLedger",
    "ReviewConflict", "ReviewThreadStore", "RunLedger", "RunManager", "TerminalManager", "TerminalSnapshot", "TaskLedger", "TaskRunner", "apply_review_action",
    "bg_concurrency", "commit_reviewed", "evaluate_file_verifier", "open_reviewed_pr", "review_diff", "review_snapshot",
    "worktree_workspace_snapshot",
    "current_failed_check_log", "current_pr_delivery",
]
