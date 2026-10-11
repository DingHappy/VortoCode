"""workflow.tasks 共用的异常类型。

叶子模块：不依赖本包任何其他模块。编排层（collaboration）和它调用的各子策略
（预算、依赖、提问……）都从这里拿异常，子策略因此不必反向依赖编排层。
"""


class CollaborationConflict(ValueError):
    """Wrong owner, stale execution or invalid lifecycle transition."""
