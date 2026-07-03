"""远端集成：把本地分支 push 上去并开 PR（gh CLI）。

这是 dev_isolated/dev_parallel 链路的最后一环——验证过的改动落到 vorto/<id> 分支后，
一键推上去并开 PR，变成可 review 的产物。**外向操作**（推到远端、建公开 PR），
调用方务必先经强确认；gh 不可用时优雅降级（只 push / 给手动开 PR 提示）。
本模块只放纯 subprocess 封装，便于 mock 测试，不依赖任何 UI。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional


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


def _gh_json(repo_root, *args) -> Optional[dict]:
    """跑一条 gh 命令并解析 JSON stdout；gh 缺失/失败/非 JSON → None。"""
    if not shutil.which("gh"):
        return None
    r = subprocess.run(["gh", *args], cwd=str(repo_root), capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout or "null")
    except (ValueError, TypeError):
        return None


_FAIL_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}


def pr_feedback(repo_root, ref: str) -> dict:
    """读一个 PR 的 review 评论 + CI 状态，汇成结构化 findings（喂给 pr_fix 的修复循环）。

    ref 可是分支名或 PR 号。走 gh CLI（与既有 open_pr 同路线、免 token 管理）。返回：
      {ok, pr, branch, comments:[{author,body,path,line,resolved}], failing_checks:[{name,link}], error}
    - review 汇总正文（CHANGES_REQUESTED / COMMENTED 且有正文）+ 行级 review 线程（GraphQL，**过滤已 resolved**）。
    - failing_checks 从 statusCheckRollup 取失败/超时/取消的检查。
    gh 不可用或该 ref 无 PR → ok=False 给提示（优雅降级）。
    """
    if not shutil.which("gh"):
        return {"ok": False, "error": "gh CLI 不可用（装 gh 且 gh auth login 后可读 PR 反馈）",
                "comments": [], "failing_checks": []}
    view = _gh_json(repo_root, "pr", "view", str(ref), "--json",
                    "number,headRefName,reviews,statusCheckRollup")
    if not view or not view.get("number"):
        return {"ok": False, "error": f"找不到 {ref} 对应的 PR（先开 PR 再收反馈）",
                "comments": [], "failing_checks": []}
    number = view["number"]
    branch = view.get("headRefName", "")
    comments = []
    for rv in (view.get("reviews") or []):                    # review 汇总正文
        body = (rv.get("body") or "").strip()
        if body and rv.get("state") in ("CHANGES_REQUESTED", "COMMENTED"):
            comments.append({"author": (rv.get("author") or {}).get("login", "?"),
                             "body": body, "path": None, "line": None, "resolved": False})
    # 行级 review 线程（GraphQL，能拿 isResolved → 过滤已解决的，只留待办的）
    repo = _gh_json(repo_root, "repo", "view", "--json", "owner,name")
    if repo and repo.get("owner"):
        q = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
             "{reviewThreads(first:100){nodes{isResolved comments(first:20){nodes"
             "{path line body author{login}}}}}}}}")
        gql = _gh_json(repo_root, "api", "graphql", "-f", f"query={q}",
                       "-F", f"o={repo['owner']['login']}", "-F", f"r={repo['name']}", "-F", f"n={number}")
        threads = (((gql or {}).get("data") or {}).get("repository") or {}).get("pullRequest") or {}
        for th in ((threads.get("reviewThreads") or {}).get("nodes") or []):
            if th.get("isResolved"):
                continue                                       # 已 resolved → 跳过（不再返工）
            for c in ((th.get("comments") or {}).get("nodes") or []):
                if (c.get("body") or "").strip():
                    comments.append({"author": (c.get("author") or {}).get("login", "?"),
                                     "body": c["body"].strip(), "path": c.get("path"),
                                     "line": c.get("line"), "resolved": False})
    failing = []
    for c in (view.get("statusCheckRollup") or []):
        concl = (c.get("conclusion") or "").upper()
        state = (c.get("state") or "").upper()
        if concl in _FAIL_CONCLUSIONS or state in _FAIL_CONCLUSIONS:
            failing.append({"name": c.get("name") or c.get("context") or "check",
                            "link": c.get("detailsUrl") or c.get("targetUrl") or ""})
    return {"ok": True, "pr": number, "branch": branch, "comments": comments,
            "failing_checks": failing, "error": ""}
