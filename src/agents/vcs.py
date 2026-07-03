"""远端集成：把本地分支 push 上去并开 PR（gh CLI）。

这是 dev_isolated/dev_parallel 链路的最后一环——验证过的改动落到 vorto/<id> 分支后，
一键推上去并开 PR，变成可 review 的产物。**外向操作**（推到远端、建公开 PR），
调用方务必先经强确认；gh 不可用时优雅降级（只 push / 给手动开 PR 提示）。
本模块只放纯 subprocess 封装，便于 mock 测试，不依赖任何 UI。
"""
from __future__ import annotations

import shutil
import subprocess


def _git(repo_root, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)


def push_branch(repo_root, branch: str, remote: str = "origin") -> dict:
    """git push -u <remote> <branch>。返回 {ok, output}。"""
    r = _git(repo_root, "push", "-u", remote, branch)
    return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).strip()[-600:]}


def open_pr(repo_root, branch: str, title: str, body: str = "", base: str = "main",
            draft: bool = False) -> dict:
    """gh pr create（需 gh 已装且已登录）。返回 {ok, url, error}；gh 不可用则 ok=False 给提示。

    draft=True → `--draft`：后台任务/自我迭代产出的 PR 默认开成 draft（人再点 ready），契合"人在合并口"。
    """
    if not shutil.which("gh"):
        return {"ok": False, "url": "", "error": "gh CLI 不可用（装 gh 且 gh auth login 后可一键开 PR）"}
    cmd = ["gh", "pr", "create", "--head", branch, "--base", base, "--title", title, "--body", body or title]
    if draft:
        cmd.append("--draft")
    r = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "url": "", "error": (r.stderr or r.stdout or "").strip()[-400:]}
    out = (r.stdout or "").strip()
    url = next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("http")), out)
    return {"ok": True, "url": url, "error": ""}


def push_and_open_pr(repo_root, branch: str, title: str, body: str = "",
                     base: str = "main", remote: str = "origin", draft: bool = False) -> dict:
    """先 push 再开 PR；push 失败就不开 PR。返回 {ok, pushed, url, error}。draft=True 开成 draft PR。"""
    pushed = push_branch(repo_root, branch, remote)
    if not pushed["ok"]:
        return {"ok": False, "pushed": False, "url": "", "error": "push 失败: " + pushed["output"]}
    pr = open_pr(repo_root, branch, title, body, base, draft=draft)
    return {"ok": pr["ok"], "pushed": True, "url": pr.get("url", ""), "error": pr.get("error", "")}
