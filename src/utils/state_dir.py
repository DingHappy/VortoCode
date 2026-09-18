"""`.vortocode/` 状态目录的看护——**只做一件事：让生成态对目标仓库的 git status 隐形。**

住在 utils 而不是 `agents/dev_plan` 里，是因为它跟 dev 流水线没有任何关系。
真机 2026-09-11 的依赖图：`src.agents.dev_plan` 被 **26 个模块**依赖，其中 **22 次只是为了
这一个函数**——gateway 的模块为了写一个 .gitignore，去 import agents 的 dev 流水线计划。
一个工具函数把两个包绑在了一起（10 个包级环里 `agents ⇄ gateway` 是最大的一个）。

**这是纯移动，实现逐字未改。** 尤其是 `_MANAGED_BEGIN` 那个标记串——已有仓库的
`.vortocode/.gitignore` 里写着它，换一个字就会让升级逻辑找不到旧区、在文件尾部**追加第二个
托管区**。97 上就有一个这样的文件。解耦不该顺手改行为。

`dev_plan` 仍然再导出，存量的 `from src.agents.dev_plan import ensure_state_gitignore`
一个字不用改——那 22 处不必同步迁移，**迁移本身才是风险**。
"""
from __future__ import annotations

from pathlib import Path

_STATE_ENTRIES = [
    ".gitignore",
    "worktrees/",
    "dev_plans/",
    "tasks/",
    "goals/",
    "runs/",
    "products/",
    "pipeline_runs/",
    "web_sessions/",
    "session_events/",
    "artifacts/",
    "states/",
    "projects/",
    "logs/",
    "vector_memory/",
    "memory/",
    "sessions.db",
    "audit.log",
    "cron_state.json",
    "cli_session.json",
    "tui_theme",
    "tui_history",
    "web_advanced_agents.json",
    "notices.jsonl",
    "review_threads.json",
    "trust.json",
    "worktree_bindings.json",
    "task_reviews/",
    # 2026-09-18 补：这几处生成态一直没进托管区，于是在**没有 gitignore .vortocode/ 的目标仓库**里
    # 原样冒进 git status——真机诊断时桌面端的改动列表里就混着 `.vortocode/journal`。
    # 判据只有一条：**谁写的**。工具写的进这里；用户手改的（permissions.yaml / hooks.yaml /
    # cron.yaml / pipelines/ / skills/ / commands/ / agents/ / AGENTS.md / BACKLOG.md /
    # HEARTBEAT.md / verify.yaml / review-policy.yaml / persona.md / instructions.md）不进。
    "journal/",
    "duty/",
    "workspaces/",
    "shots/",
    "shadow.git/",
    "decisions.json",
    "published_urls.json",
    "signals_state.json",
    "settings.json",
    "memory.md",
]

_MANAGED_BEGIN = "# >>> vortocode managed —— 自动维护区，勿手改（升级会重写本区）；自定义规则请写在区外 >>>"
_MANAGED_END = "# <<< vortocode managed <<<"


def _managed_block() -> str:
    return "\n".join([
        _MANAGED_BEGIN,
        "# VortoCode 自动生成的运行时状态——不进版本控制。",
        "# 用户配置（permissions.yaml / hooks.yaml / review-policy.yaml / cron.yaml / HEARTBEAT.md / BACKLOG.md /",
        "# commands/ / skills/ / AGENTS.md 等）不在此列，可自行 git add。",
        *_STATE_ENTRIES,
        _MANAGED_END,
    ])


def ensure_state_gitignore(repo_root: str) -> None:
    """在 .vortocode/ 放一个自忽略的 .gitignore：工具生成态对目标仓库 git status 隐形、用户配置照常可版本化。

    .vortocode/ 混放了生成态（worktrees/dev_plans/tasks/…）与用户配置（permissions.yaml/commands/…）：
    前者不该进用户的版本控制，后者用户可能想 commit。放这个**选择性**忽略清单，让 dev_auto/后台任务/cron
    在任何目标仓库（无论其有没有 gitignore .vortocode/）都不污染 git status，同时不挡用户版本化自己的配置。

    托管清单走 **managed block**（#140 评审：只写新文件的话，清单升级永远到不了老仓库）：
    - 标记区（_MANAGED_BEGIN…END）内容由我们幂等升级——清单加了新条目，老仓库下次任何写入点触发即补齐；
    - 标记区**外**的内容（用户自定义）原样保留；想覆盖托管规则可在区后写否定规则（gitignore 后行优先）；
    - 旧版无标记文件：托管条目全齐则不动（grandfather），缺条目才在尾部追加托管区（用户内容逐字保留）。
    best-effort（IO 出错不影响真正落盘）。凡往 .vortocode/ 落生成态的写入点都应先调它。
    """
    try:
        d = Path(repo_root) / ".vortocode"
        gi = d / ".gitignore"
        block = _managed_block()
        if not gi.exists():
            d.mkdir(parents=True, exist_ok=True)
            gi.write_text(block + "\n", encoding="utf-8")
            return
        cur = gi.read_text(encoding="utf-8")
        if _MANAGED_BEGIN in cur and _MANAGED_END in cur:
            pre, rest = cur.split(_MANAGED_BEGIN, 1)
            _, post = rest.split(_MANAGED_END, 1)
            new = pre + block + post                  # 只重写标记区，区外原样
            if new != cur:
                gi.write_text(new, encoding="utf-8")
        else:
            have = {ln.strip() for ln in cur.splitlines()}
            if all(e in have for e in _STATE_ENTRIES):
                return                                # 旧文件已覆盖全部托管条目 → 不动
            gi.write_text(cur.rstrip("\n") + "\n\n" + block + "\n", encoding="utf-8")
    except OSError:
        pass
