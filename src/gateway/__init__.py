"""VortoCode gateway：会话装配与传输层。

只放「把 MainAgent 装成一个会话、并把它接到某个端」的东西：会话装配工厂（agent_session）、
会话表/前台回合归属（sessions/session_actor）、事件流与协议（session_events/protocol/client）、
运行时生命周期（runtime/process_lease/supervisor_watchdog）、IM 内嵌服务与审计台账。

业务能力不在这里：后台任务与协作在 `src.workflow.tasks`，Git 评审与交付在 `src.workflow.review`，
Desktop 工作台（目标/命令运行/终端/日志）在 `src.workflow.workbench`，非代码流水线在
`src.workflow.pipeline`，定时与值班在 `src.scheduling`。
"""
