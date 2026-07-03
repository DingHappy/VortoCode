"""VortoCode gateway：常驻后台的任务运行时（D1 起步）。

先落地"后台任务 + write-ahead 台账 + 崩溃可恢复"这一层（tasks.py）：长流水线不再占住一个交互回合
十几分钟，而是提交即返回、后台跑、随时查询/取消、进程崩溃后可恢复（running→interrupted，接 C1 的
dev_resume 续跑）。cron/heartbeat（D3）与单核多前端（B2 长期项）在其上迭代。
"""

from .tasks import BackgroundTask, TaskLedger, TaskRunner, bg_concurrency

__all__ = ["BackgroundTask", "TaskLedger", "TaskRunner", "bg_concurrency"]
