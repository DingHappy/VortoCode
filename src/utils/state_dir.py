"""`.vortoone/` 状态目录：在哪、怎么从旧名字搬过来，以及让生成态对目标仓库的 git status 隐形。

## 目录名（ADR-018 批次 C，2026-11-08）

状态目录从 `.vortocode` 改名为 `.vortoone`。凡是拼 `<根目录>/.vortoone` 的地方都走 :func:`state_dir`：
第一次碰到某个根目录下还是旧目录时，整个改名过去，并在原处留一个指向新目录的链接
（`.vortocode -> .vortoone`），数据里存着的旧绝对路径（worktree、制品、会话里的路径）照样能打开。

- 新旧都在：以新目录为准，旧目录不动，打一次告警。
- 搬不动（只读、权限）：继续用旧目录。
- 根目录是 git 仓库、且主人把 `.vortocode/` 里的文件提交进了仓库：**不搬**，继续用旧目录——
  改名会让那些文件在 git 里显示成删除。
- 搬过之后在仓库自己的 `info/exclude` 里藏起留下的链接；旧目录原本被 git 忽略的，新目录也一并忽略，
  主人看到的 git status 跟搬之前一样。

`~/Library/Application Support/VortoCode` → `VortoOne` 用同一套规则（:func:`app_support_dir`）。
判断「这是 VortoOne 自己的状态目录」的地方（敏感路径、遍历时跳过）两个名字都要认：:data:`STATE_DIRNAMES`。

## gitignore 看护

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

import logging
import os
import subprocess
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

STATE_DIRNAME = ".vortoone"
LEGACY_STATE_DIRNAME = ".vortocode"
STATE_DIRNAMES = frozenset({STATE_DIRNAME, LEGACY_STATE_DIRNAME})
APP_SUPPORT_DIRNAME = "VortoOne"
LEGACY_APP_SUPPORT_DIRNAME = "VortoCode"

_conflicts_reported: set = set()


def state_dir(root: Union[str, Path]) -> Path:
    """`<root>/.vortoone`；旧的 `<root>/.vortocode` 还在就先搬过来（规则见模块说明）。"""
    return _migrated(Path(root), LEGACY_STATE_DIRNAME, STATE_DIRNAME, git_aware=True)


def home_state_dir() -> Path:
    """用户级状态目录 `~/.vortoone`。"""
    return state_dir(Path.home())


def app_support_dir() -> Path:
    """macOS 的 `~/Library/Application Support/VortoOne`（旧名 VortoCode，同样自动搬）。"""
    base = Path.home() / "Library" / "Application Support"
    return _migrated(base, LEGACY_APP_SUPPORT_DIRNAME, APP_SUPPORT_DIRNAME, git_aware=False)


def xdg_dir(env_name: str, default: Path) -> Path:
    """Linux 上 XDG 目录里的 `vortoone/`（旧名 `vortocode/`，同样自动搬）。"""
    base = Path(os.getenv(env_name) or default)
    return _migrated(base, "vortocode", "vortoone", git_aware=False)


def _migrated(base: Path, old_name: str, new_name: str, *, git_aware: bool) -> Path:
    new, old = base / new_name, base / old_name
    try:
        if old.is_symlink() or not old.is_dir():
            return new                                 # 已经搬过，或者本来就没有旧目录
        if os.path.lexists(new):
            key = str(old)
            if key not in _conflicts_reported:
                _conflicts_reported.add(key)
                logger.warning("%s 和 %s 都在：用新目录，旧目录里的东西没有搬", new, old)
            return new
        ignored = False
        if git_aware and (base / ".git").exists():
            tracked, ignored = _git_view(base, old_name)
            if tracked is not False:
                return old                             # 有提交进仓库的文件，或者看不清：不搬
        os.rename(old, new)
    except FileNotFoundError:
        return new                                     # 别的进程刚搬完
    except OSError as error:
        logger.warning("状态目录 %s 没能改名为 %s（%s），继续用旧目录", old, new, error)
        return old
    try:
        os.symlink(new_name, old)
    except OSError:
        pass                                           # 别的进程已经留了链接，或者不让建
    if git_aware and (base / ".git").exists():
        _hide_in_git(base, old_name, new_name if ignored else None)
    return new


def _git(base: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(base), *args], capture_output=True, text=True, timeout=10)


def _git_view(base: Path, old_name: str):
    """(旧目录里有没有提交进仓库的文件, 旧目录是不是被忽略)。git 用不了时第一项是 None。"""
    try:
        files = _git(base, "ls-files", "-z", "--", old_name)
        if files.returncode != 0:
            return None, False
        ignored = _git(base, "check-ignore", "-q", f"{old_name}/").returncode == 0
        return bool(files.stdout), ignored
    except (OSError, subprocess.SubprocessError):
        return None, False


def _hide_in_git(base: Path, old_name: str, also_ignore: str | None) -> None:
    """在仓库的 info/exclude 里藏起留下的链接（以及原本就被忽略的新目录）。只改本机，不进提交。"""
    try:
        common = _git(base, "rev-parse", "--git-common-dir")
        if common.returncode != 0:
            return
        exclude = (base / common.stdout.strip()).resolve() / "info" / "exclude"
        have = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
        wanted = [f"/{old_name}"] + ([f"/{also_ignore}/"] if also_ignore else [])
        missing = [line for line in wanted if line not in have]
        if missing:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            text = "\n".join(have).rstrip("\n")
            note = "# VortoOne：状态目录改名为 .vortoone 后留下的旧名链接（ADR-018）"
            exclude.write_text((text + "\n" if text else "") + "\n".join([note, *missing]) + "\n", encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        pass


_STATE_ENTRIES = [
    ".gitignore",
    "worktrees/",
    "dev_plans/",
    "tasks/",
    "teams/",
    "dispatches/",
    "codex/",
    "task_scans/",
    "goals/",
    "undo/",
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
    "followups.json",
    "ops_heartbeats.json",
    "cli_session.json",
    "tui_theme",
    "tui_history",
    "web_advanced_agents.json",
    "notices.jsonl",
    "review_threads.json",
    "trust.json",
    "worktree_bindings.json",
    "task_reviews/",
    "im-pairings/",
    "im-bots/",                # bot-scoped personas and hello state, private to the current instance
    "mobile-devices/",         # hashed mobile device grants; never part of task/session state
    "isolated_deliveries/",
    "runtime.lock",
    "main_chat.json",          # 常驻助理的主要聊天登记（Desktop 连上时写，手机/电脑共用）
    "personal-skills/",        # 助理个人指令及版本审批，仅保存在当前实例的数据目录
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
        "# VortoOne 自动生成的运行时状态——不进版本控制。",
        "# 用户配置（permissions.yaml / hooks.yaml / review-policy.yaml / cron.yaml / HEARTBEAT.md / BACKLOG.md /",
        "# commands/ / skills/ / AGENTS.md 等）不在此列，可自行 git add。",
        *_STATE_ENTRIES,
        _MANAGED_END,
    ])


def ensure_state_gitignore(repo_root: str) -> None:
    """在 .vortoone/ 放一个自忽略的 .gitignore：工具生成态对目标仓库 git status 隐形、用户配置照常可版本化。

    .vortoone/ 混放了生成态（worktrees/dev_plans/tasks/…）与用户配置（permissions.yaml/commands/…）：
    前者不该进用户的版本控制，后者用户可能想 commit。放这个**选择性**忽略清单，让 dev_auto/后台任务/cron
    在任何目标仓库（无论其有没有 gitignore .vortoone/）都不污染 git status，同时不挡用户版本化自己的配置。

    托管清单走 **managed block**（#140 评审：只写新文件的话，清单升级永远到不了老仓库）：
    - 标记区（_MANAGED_BEGIN…END）内容由我们幂等升级——清单加了新条目，老仓库下次任何写入点触发即补齐；
    - 标记区**外**的内容（用户自定义）原样保留；想覆盖托管规则可在区后写否定规则（gitignore 后行优先）；
    - 旧版无标记文件：托管条目全齐则不动（grandfather），缺条目才在尾部追加托管区（用户内容逐字保留）。
    best-effort（IO 出错不影响真正落盘）。凡往 .vortoone/ 落生成态的写入点都应先调它。
    """
    try:
        d = state_dir(repo_root)
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
