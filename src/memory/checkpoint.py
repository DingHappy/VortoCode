"""回合级工作区快照（rewind v2）——影子 git 仓库，补上 shell 副作用这一块。

`/rewind`（[edits 表](rewind.py)）只记工具写入：`run_command` 跑 `black .`、代码生成脚本、
`npm install` 改了源码，撤不回来。这里在**独立的影子 git 仓库**里给整个工作区打回合快照，
`/checkpoint restore` 能整树还原——不论改动出自工具、shell 还是子进程。

**绝不碰用户的 .git**：影子仓库的 GIT_DIR 是 `.vortocode/shadow.git`（gitignored），
work-tree 指向仓库根。用户的 index / HEAD / refs / stash 一个字节都不动，`git log` 里
也不会多出快照提交。

**只覆盖 git 可见文件**：影子仓库照样读用户的 .gitignore（work-tree 里就有），所以
node_modules / 构建产物不进快照（否则 `npm install` 后一次快照能跑几分钟）。也就是说
它覆盖的正是"源码被 shell 改坏"这一类——而那正是要救的场景。

**两条 rewind 的分工（刻意不合并）**：
- `/rewind`（edits 表）：**精细、保守**——逐文件核对"内容还是我写的那份吗"，被手改过就跳过。
- `/checkpoint restore`（本模块）：**整树、强力**——能救 shell 副作用，但代价是**整树覆盖**，
  快照之后的一切改动（包括用户手改）都会被冲掉。故必须先 `preview` 列清单、显式确认再动手。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

_SHADOW_DIRNAME = "shadow.git"
_MAX_SNAPSHOTS = 50            # 只留最近 N 个回合快照（更早的 ref 删掉，对象由 gc 回收）


def checkpoints_enabled() -> bool:
    """VORTOCODE_CHECKPOINT=0/false/no/off 关闭（大仓库嫌每回合 add -A 慢时）。默认开。"""
    v = (os.getenv("VORTOCODE_CHECKPOINT") or "").strip().lower()
    return v not in ("0", "false", "no", "off")


def _shadow_dir(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / _SHADOW_DIRNAME


def _run(repo_root: str, shadow: Path, *args: str, check: bool = False):
    """在影子仓库上跑 git：GIT_DIR=影子、work-tree=仓库根。**绝不用 `git -C`**——那会碰用户的 .git。"""
    env = {**os.environ, "GIT_DIR": str(shadow), "GIT_WORK_TREE": str(repo_root),
           # 提交身份与用户无关，也别读用户的 hooks/模板
           "GIT_AUTHOR_NAME": "vortocode", "GIT_AUTHOR_EMAIL": "vortocode@local",
           "GIT_COMMITTER_NAME": "vortocode", "GIT_COMMITTER_EMAIL": "vortocode@local"}
    return subprocess.run(["git", *args], cwd=str(repo_root), env=env,
                          capture_output=True, text=True, check=check, timeout=120)


def _ensure_shadow(repo_root: str) -> Optional[Path]:
    """惰性初始化影子仓库（bare 式 GIT_DIR，但有自己的 index）。失败返回 None（快照是旁路，不拦回合）。"""
    shadow = _shadow_dir(repo_root)
    if (shadow / "HEAD").is_file():
        return shadow
    try:
        shadow.mkdir(parents=True, exist_ok=True)
        r = _run(repo_root, shadow, "init", "--quiet")
        if r.returncode != 0:
            return None
        # 影子仓库额外排除 .vortocode/ 自身（快照里存快照 = 递归膨胀）
        info = shadow / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "exclude").write_text(".vortocode/\n", encoding="utf-8")
        return shadow
    except Exception:  # noqa: BLE001
        return None


def snapshot(repo_root: str, label: str) -> Optional[str]:
    """给当前工作区打一个快照，返回 commit sha（失败/关闭返回 None——旁路，绝不拦回合）。

    label 进提交信息，供 `/checkpoint` 列表展示（如用户那句话的前 60 字）。
    工作区与上个快照完全一致时**不重复提交**（返回既有 HEAD）——空回合不刷屏。
    """
    if not checkpoints_enabled():
        return None
    shadow = _ensure_shadow(repo_root)
    if shadow is None:
        return None
    try:
        if _run(repo_root, shadow, "add", "-A").returncode != 0:
            return None
        # 无变化就不提交（git commit 会以非零退出报 "nothing to commit"）
        head = _run(repo_root, shadow, "rev-parse", "HEAD")
        if head.returncode == 0 and _run(repo_root, shadow, "diff", "--cached",
                                         "--quiet").returncode == 0:
            return (head.stdout or "").strip() or None
        msg = (label or "snapshot").replace("\n", " ")[:200]
        if _run(repo_root, shadow, "commit", "-q", "-m", msg,
                "--allow-empty-message").returncode != 0:
            return None
        sha = (_run(repo_root, shadow, "rev-parse", "HEAD").stdout or "").strip()
        _prune(repo_root, shadow)
        return sha or None
    except Exception:  # noqa: BLE001
        return None


def _prune(repo_root: str, shadow: Path) -> None:
    """只留最近 _MAX_SNAPSHOTS 个：把 HEAD 往回截断到第 N 个祖先（对象留给 gc）。"""
    try:
        r = _run(repo_root, shadow, "rev-list", "--count", "HEAD")
        n = int((r.stdout or "0").strip() or 0)
        if n <= _MAX_SNAPSHOTS:
            return
        keep = _run(repo_root, shadow, "rev-parse", f"HEAD~{_MAX_SNAPSHOTS - 1}")
        base = (keep.stdout or "").strip()
        if base:                      # 以该提交为新根：软重写 refs（历史更早的快照不可达 → 可回收）
            _run(repo_root, shadow, "replace", "--graft", base)
    except Exception:  # noqa: BLE001
        pass


def list_snapshots(repo_root: str, limit: int = 20) -> List[Dict[str, Any]]:
    """列出快照（新 → 旧）：[{sha, short, at, label}]。无影子仓库返回 []。"""
    shadow = _shadow_dir(repo_root)
    if not (shadow / "HEAD").is_file():
        return []
    r = _run(repo_root, shadow, "log", f"-{int(limit)}", "--pretty=format:%H\x1f%h\x1f%ci\x1f%s")
    if r.returncode != 0:
        return []
    out: List[Dict[str, Any]] = []
    for line in (r.stdout or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            out.append({"sha": parts[0], "short": parts[1], "at": parts[2], "label": parts[3]})
    return out


def changed_since(repo_root: str, sha: str) -> List[str]:
    """相对某快照，当前工作区有哪些文件不同（还原会覆盖它们——**必须先给人看**）。"""
    shadow = _shadow_dir(repo_root)
    if not (shadow / "HEAD").is_file():
        return []
    _run(repo_root, shadow, "add", "-A")            # 刷新影子 index，才比得准
    r = _run(repo_root, shadow, "diff", "--cached", "--name-only", sha)
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def restore(repo_root: str, sha: str) -> Dict[str, Any]:
    """把工作区整树还原到某快照。返回 {ok, restored:[文件], error}。

    **整树覆盖**：快照之后对 git 可见文件的一切改动都会被冲掉（包括用户手改）——调用方必须
    先用 changed_since() 列清单 + 显式确认。未被快照跟踪的新文件（如 .gitignore 掉的产物）
    不动，故不会误删 node_modules 之类。
    """
    shadow = _shadow_dir(repo_root)
    if not (shadow / "HEAD").is_file():
        return {"ok": False, "restored": [], "error": "没有快照（影子仓库不存在）"}
    # 先验 sha 确实是个快照提交：否则 changed_since 的 diff 会失败→返回空→这里误报"无需还原"，
    # 把"快照不存在/编号写错"粉饰成成功（自审逮到：静默失败比报错更坏）。
    if _run(repo_root, shadow, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        return {"ok": False, "restored": [], "error": f"快照不存在: {sha[:12]}"}
    files = changed_since(repo_root, sha)
    if not files:
        return {"ok": True, "restored": [], "error": ""}
    # checkout 把快照里的版本写回工作区；快照之后**新增**的文件 checkout 不会删 → 显式清掉，
    # 否则"还原"只还原了修改、留下一地新文件（还原得不干净，人会以为撤销失败）。
    r = _run(repo_root, shadow, "checkout", "-f", sha, "--", ".")
    if r.returncode != 0:
        return {"ok": False, "restored": [], "error": (r.stderr or "checkout 失败").strip()[:200]}
    in_snap = set()
    ls = _run(repo_root, shadow, "ls-tree", "-r", "--name-only", sha)
    if ls.returncode == 0:
        in_snap = {ln.strip() for ln in (ls.stdout or "").splitlines() if ln.strip()}
    for rel in files:
        if rel in in_snap:
            continue
        p = Path(repo_root) / rel                    # 快照里没有 → 是之后新建的，删掉
        try:
            if p.is_file():
                p.unlink()
        except Exception:  # noqa: BLE001
            pass
    _run(repo_root, shadow, "reset", "-q", "--mixed", sha)   # 影子 index 对齐，免得下次 diff 花掉
    return {"ok": True, "restored": files, "error": ""}
