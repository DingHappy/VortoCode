"""文件面工具：读 / 写 / 跑测试，外加路径围栏 `_resolve_within`。

`_resolve_within` 是全仓的路径安全地基（一切文件工具都必须先过它，把路径钉死在工作目录内），
和读写工具放在一起是为了让"围栏"与"用围栏的人"同处一屏——分开放最容易出现新工具
忘了过围栏。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from src.agents.tool import Tool
from src.agents.tools._common import (_image_exts, _max_image_bytes, _max_read_file,
                                      _truthy)


def _glob_to_regex(pattern: str) -> "re.Pattern":
    """把含 '/' 的 glob 译成正则（真·glob 语义）：`**/`=任意层目录(含零层)、`**`=跨 / 任意、
    `*`=段内任意(不跨 /)、`?`=段内单字符。比 fnmatch 强在 `src/**/*.ts` 能匹配 src 下任意深度。"""
    i, n, out = 0, len(pattern), []
    while i < n:
        if pattern[i] == "*":
            if pattern[i:i + 3] == "**/":
                out.append(r"(?:.*/)?"); i += 3; continue
            if pattern[i:i + 2] == "**":
                out.append(r".*"); i += 2; continue
            out.append(r"[^/]*"); i += 1; continue
        if pattern[i] == "?":
            out.append(r"[^/]"); i += 1; continue
        out.append(re.escape(pattern[i])); i += 1
    return re.compile("(?s:" + "".join(out) + r")\Z")


def _edit_miss_hint(rel: str, text: str, old: str) -> str:
    """old 没匹配上时，给一条**能据以恢复**的提示，而不是一句"找不到"。

    真机 2026-09-17（打包版冒烟）：agent 改完第一处后第二次 edit_file 没匹配上，只收到
    "在 todo.py 中找不到要替换的原文（old）"——既不知道是缩进差了、还是文件已被自己改过、
    还是路径给错了，于是它直接放弃了整个任务。
    """
    first = next((line for line in old.splitlines() if line.strip()), "").strip()
    where = ""
    if first:
        for number, line in enumerate(text.splitlines(), 1):
            if first in line:
                where = (f" 文件里能找到 `{first[:60]}`（第 {number} 行），"
                         f"多半是缩进/空行/上下文对不上——old 必须**逐字**匹配（含前导空格）。")
                break
        else:
            where = f" 文件里也没有 `{first[:60]}` 这一行，先确认是不是改错了文件。"
    return (f"在 {rel} 中找不到要替换的原文（old）。{where}"
            f" 请先 read_file 看当前内容（可能已被上一次编辑改过），再用真实存在的片段重试。")

def _resolve_within(base: Any, rel: Any) -> Optional["Path"]:
    """把相对路径解析进 base 并做边界校验：`..` 越界、绝对路径、软链逃逸都返回 None。

    读工具与写工具**共用同一套防护**——避免"写路径防了、读路径没防"的不对称
    （否则 read_file("../../etc/passwd") 或绝对路径能读仓库外任意文件）。
    用 resolve() 后 relative_to(base) 判定：绝对路径会被 `base / "/x"` 语义丢掉 base
    → 落到根 → relative_to 抛 ValueError；`..` 与软链在 resolve() 后同样落到 base 外被拒。
    """
    from src.utils.paths import resolve_within

    # `@src/x.py` 是 **agents 层的书写约定**，剥离留在这里做——它不属于围栏本身，
    # 塞进围栏就等于让 web/memory 也悄悄接受 `@` 前缀。围栏只管边界。
    return resolve_within(base, str(rel or "").strip().lstrip("@"))


def build_read_tools(repo_root: str) -> list[Tool]:
    """构建一组只读工具（read_file/list_files/grep/analyze_repo），UI 无关，供 TUI/Web 共用。"""
    from pathlib import Path

    # 全仓库文本文件（grep/list_files 用）：跳过噪音目录、只收文本类扩展名——
    # 之前只扫 src/tests 的 .py，搜不到 web/html、examples、docs、配置等，是多语言仓库的大盲区。
    _SKIP_DIRS = {".git", "node_modules", "__pycache__", ".vortocode", ".venv", "venv",
                  "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                  ".idea", ".vscode", "htmlcov", ".eggs", "site-packages"}
    _TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".md", ".rst",
                 ".yaml", ".yml", ".toml", ".json", ".txt", ".cfg", ".ini", ".sh", ".sql", ".env"}

    def _rel_posix(path: Path, base: Path) -> str:
        return path.relative_to(base).as_posix()

    def _skip_builtin(rel: str) -> bool:
        return any(part in _SKIP_DIRS for part in Path(rel).parts)

    def _under_dir(rel: str, sub: str) -> bool:
        if not sub:
            return True
        sub = sub.strip("/")
        return rel == sub or rel.startswith(sub + "/")

    def _normalize_dir_arg(value: Any) -> tuple[str, str]:
        """Validate a dir argument and normalize it to repo-relative POSIX form.

        Returns (normalized_subdir, bad_value). bad_value is non-empty when the input points
        outside the repository. "." becomes "" so callers search the whole repo.
        """
        raw = str(value or "").strip().lstrip("@")
        if not raw:
            return "", ""
        p = _resolve_within(repo_root, raw)
        if p is None:
            return "", raw
        try:
            rel = p.relative_to(Path(repo_root).resolve()).as_posix()
        except (ValueError, OSError):
            return "", raw
        if rel == ".":
            return "", ""
        return rel.strip("/"), ""

    def _git_visible_files(base: Path) -> list[str] | None:
        """Return git-visible files, respecting .gitignore/info excludes/global excludes.

        `git ls-files --cached --others --exclude-standard` gives the exact file set a developer
        expects: tracked files plus untracked non-ignored files. This avoids walking build caches
        and generated outputs into list_files/glob/grep.
        """
        import subprocess
        try:
            r = subprocess.run(
                ["git", "-C", str(base), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                capture_output=True,
                timeout=3,
            )
        except Exception:  # noqa: BLE001
            return None
        if r.returncode != 0:
            return None
        out: list[str] = []
        for raw in r.stdout.split(b"\0"):
            if not raw:
                continue
            try:
                rel = raw.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            if rel and not _skip_builtin(rel) and (base / rel).is_file():
                out.append(rel)
        return sorted(set(out))

    def _load_root_gitignore(base: Path):
        """Small fallback matcher for non-git directories.

        It intentionally covers common .gitignore forms (comments, negation, anchored paths,
        directory patterns, basename globs). Git repositories use `git ls-files`, so the fallback
        only needs to keep non-git workspaces from reading obvious ignored output.
        """
        import fnmatch
        rules: list[tuple[bool, str, bool, bool]] = []
        p = base / ".gitignore"
        if not p.is_file():
            return lambda _rel, is_dir=False: False
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            neg = s.startswith("!")
            if neg:
                s = s[1:].strip()
            if not s:
                continue
            anchored = s.startswith("/")
            s = s.lstrip("/")
            dir_only = s.endswith("/")
            s = s.rstrip("/")
            if s:
                rules.append((neg, s, anchored, dir_only))

        def _ignored(rel: str, is_dir: bool = False) -> bool:
            rel = rel.replace("\\", "/").strip("/")
            ignored = False
            for neg, pat, anchored, dir_only in rules:
                if dir_only and not (is_dir or rel.startswith(pat.rstrip("/") + "/")):
                    continue
                if anchored or "/" in pat:
                    match = fnmatch.fnmatch(rel, pat) or rel.startswith(pat.rstrip("/") + "/")
                else:
                    parts = rel.split("/")
                    match = any(fnmatch.fnmatch(part, pat) for part in parts)
                if match:
                    ignored = not neg
            return ignored

        return _ignored

    def _all_files() -> list[str]:
        import os
        from src.agents.capabilities import is_sensitive_repo_path
        base = Path(repo_root)
        git_files = _git_visible_files(base)
        if git_files is not None:
            return [rel for rel in git_files if not is_sensitive_repo_path(rel, repo_root)]
        ignored = _load_root_gitignore(base)
        out: list[str] = []
        for root, dirs, files in os.walk(base):
            kept_dirs = []
            for d in dirs:
                rel_dir = _rel_posix(Path(root) / d, base)
                if d in _SKIP_DIRS or ignored(rel_dir, is_dir=True):
                    continue
                kept_dirs.append(d)
            dirs[:] = kept_dirs                         # 原地剪枝：不下钻噪音/忽略目录
            for fn in files:
                fp = Path(root) / fn
                rel = _rel_posix(fp, base)
                if _skip_builtin(rel) or ignored(rel, is_dir=False):
                    continue
                if not is_sensitive_repo_path(rel, repo_root):
                    out.append(rel)
                if len(out) >= 6000:
                    return sorted(out)
        return sorted(out)

    def _files() -> list[str]:
        out: list[str] = []
        for rel in _all_files():
            if Path(rel).suffix.lower() in _TEXT_EXT:
                out.append(rel)
                if len(out) >= 4000:
                    return sorted(out)
        return sorted(out)

    def _int(v):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return None

    async def _read_file(args: dict) -> "str | dict":
        rel = str(args.get("path", "")).strip().lstrip("@")
        if not rel:
            return "缺少 path 参数。"
        p = _resolve_within(repo_root, rel)
        if p is None:
            return f"路径越界或非法（只能读仓库内文件）: {rel}"
        if not p.is_file():
            return f"(不存在: {rel})"
        # 图片：不走 utf-8 文本（那只会 UnicodeDecodeError），作为图片附件注入本回合上下文——
        # 模型本来就能看图（CLI -i 的同一条 image_block 管线），此前只是 agent 自己读不进来：
        # 前端截图、报错截图、Browser Verify 自己截的图，读了却看不见。
        if p.suffix.lower() in _image_exts():
            size = p.stat().st_size
            cap = _max_image_bytes()
            if size > cap:
                return (f"(图片过大: {rel} 共 {size // 1024} KB，超过上限 {cap // 1024} KB，未注入；"
                        f"可设 VORTOCODE_MAX_IMAGE_BYTES 调整)")
            return {"text": f"(已读取图片 {rel}，{max(1, size // 1024)} KB；"
                            "内容已作为图片附件注入本回合上下文，直接查看即可)",
                    "images": [str(p)]}
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return f"读取失败: {e}"
        start = _int(args.get("start"))
        # 给了 start：读指定行段（1-based，含端点）——接上 find_definition/document_symbols 给的行号
        if start is not None:
            lines = text.splitlines()
            s = max(1, start)
            end = _int(args.get("end"))
            e = min(len(lines), end if (end is not None and end >= s) else s + 120)   # 默认约 120 行
            cap = _max_read_file()
            chunk = "\n".join(lines[s - 1:e])[:cap]
            return f"# {rel} 第 {s}–{e} 行（共 {len(lines)} 行）\n{chunk}"
        # 无 start：整文件；超长截断并提示用 start/end 读指定行段（别只能看开头）
        cap = _max_read_file()
        if len(text) > cap:
            total = text.count("\n") + 1
            return (f"# {rel}（共 {total} 行，过长，仅显示前部；用 start/end 读指定行段）\n"
                    f"{text[:cap]}\n…(已截断，用 read_file(path, start, end) 读更后面)")
        return text

    async def _list_files(args: dict) -> str:
        sub, bad = _normalize_dir_arg(args.get("dir", ""))
        if bad:
            return f"dir 越界或非法（只能在仓库内列出）: {bad}"
        fs = [f for f in _files() if _under_dir(f, sub)] if sub else _files()
        return "\n".join(fs[:200]) if fs else "(无源码文件)"

    async def _glob(args: dict) -> str:
        """按文件名 glob 找文件（对标 CC 的 Glob）：不限文本扩展名、跳噪音目录、按最近修改排序。

        pattern 不含 '/' → 匹配**文件名**（最常用，如 `*.ts`/`*.test.js`/`conftest.py`，任意深度）；
        含 '/' → 匹配相对路径全程（`**` 为 best-effort）。可选 dir 限定子目录。
        """
        import fnmatch
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            return "glob 需要 pattern（如 *.ts、**/*.test.js、src/**/*.py）。"
        sub, bad = _normalize_dir_arg(args.get("dir", ""))
        base = Path(repo_root)
        if bad:                                           # dir 不得指向仓库外（防 os.walk 逃逸）
            return f"dir 越界或非法（只能在仓库内查找）: {bad}"
        slash_re = _glob_to_regex(pattern) if "/" in pattern else None

        def _match(rel_posix: str) -> bool:
            if slash_re is not None:                     # 含 / → 全路径真·glob（** 跨目录）
                return slash_re.match(rel_posix) is not None
            return fnmatch.fnmatch(Path(rel_posix).name, pattern)   # 否则匹配文件名、任意深度

        hits: list[tuple[float, str]] = []
        for rel in _all_files():
            if not _under_dir(rel, sub):
                continue
            if _match(rel):
                try:
                    mtime = (base / rel).stat().st_mtime
                except OSError:
                    mtime = 0.0
                hits.append((mtime, rel))
                if len(hits) >= 5000:                # 防超大仓走查爆内存
                    break
        if not hits:
            return f"没有匹配 `{pattern}` 的文件{('（在 ' + sub + '/ 下）') if sub else ''}。"
        hits.sort(key=lambda t: t[0], reverse=True)      # 最近修改的排前（像 CC，方便找刚动过的）
        shown = [r for _m, r in hits[:200]]
        tail = f"\n…(共 {len(hits)} 个，只列最近 200)" if len(hits) > 200 else ""
        return "\n".join(shown) + tail

    async def _grep(args: dict) -> str:
        pat = str(args.get("pattern", "")).strip()
        if not pat:
            return "缺少 pattern 参数。"
        try:
            rx = re.compile(pat)
        except re.error as e:
            return f"无效正则: {e}"
        sub, bad = _normalize_dir_arg(args.get("dir", ""))       # 此前 dir 被宣传却没生效→在此兜上
        if bad:
            return f"dir 越界或非法（只能在仓库内搜索）: {bad}"
        files = [f for f in _files() if _under_dir(f, sub)] if sub else _files()
        ctx = max(0, min(_int(args.get("context")) or 0, 5))     # 上下文行数（±N），上限 5 防输出爆炸
        base = Path(repo_root)

        if ctx == 0:                                             # 紧凑模式（默认）：path:line: text 单行命中
            hits: list[str] = []
            for f in files:
                try:
                    lines = (base / f).read_text(encoding="utf-8", errors="ignore").splitlines()
                except Exception:  # noqa: BLE001
                    continue
                for i, line in enumerate(lines, 1):
                    if rx.search(line):
                        hits.append(f"{f}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 100:
                            return "\n".join(hits)
            return "\n".join(hits) if hits else f"没有匹配 /{pat}/ 的内容。"

        # context > 0：按文件分组、合并相邻窗口、标出命中行（> 前缀），类似 ripgrep -C，定位后不必再 read_file
        out: list[str] = []
        for f in files:
            try:
                lines = (base / f).read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:  # noqa: BLE001
                continue
            matches = [i for i, line in enumerate(lines) if rx.search(line)]   # 0-based 命中行
            if not matches:
                continue
            ranges: list[list[int]] = []                         # 把每个命中的 ±ctx 窗口合并、相邻即并
            for m in matches:
                lo, hi = max(0, m - ctx), min(len(lines) - 1, m + ctx)
                if ranges and lo <= ranges[-1][1] + 1:
                    ranges[-1][1] = max(ranges[-1][1], hi)
                else:
                    ranges.append([lo, hi])
            mset = set(matches)
            out.append(f"{f}:")
            for ri, (lo, hi) in enumerate(ranges):
                if ri:
                    out.append("   ⋯")                           # 同文件内不连续窗口的分隔
                for ln in range(lo, hi + 1):
                    mark = ">" if ln in mset else " "
                    out.append(f"{ln + 1:>5} {mark} {lines[ln][:200]}")
            out.append("")
            if sum(len(x) for x in out) > 6000:                  # 总输出兜底，防爆
                out.append("…(结果较多，已截断；缩小 pattern、给 dir 或调小 context)")
                break
        return "\n".join(out).rstrip() if out else f"没有匹配 /{pat}/ 的内容。"

    async def _analyze_repo(args: dict) -> str:
        from src.orchestrator.self_analysis import analyze_self, render_report
        return render_report(await analyze_self(repo_root))

    async def _find_definition(args: dict) -> str:
        from src.agents.lsp import find_definition
        return find_definition(repo_root, str(args.get("symbol", "")))

    async def _find_references(args: dict) -> str:
        from src.agents.lsp import find_references
        return find_references(repo_root, str(args.get("symbol", "")))

    async def _document_symbols(args: dict) -> str:
        from src.agents.lsp import document_symbols
        rel = str(args.get("path", "")).strip().lstrip("@")
        if rel and _resolve_within(repo_root, rel) is None:   # 与 read_file 同一边界防护
            return f"路径越界或非法（只能读仓库内文件）: {rel}"
        return document_symbols(repo_root, rel)

    def _git_ro(*a):
        """只读 git：在 repo_root 跑，超时/出错都安全返回 CompletedProcess-ish。"""
        import subprocess
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", repo_root, *a],
            capture_output=True, text=True, timeout=20,
        )

    async def _git_status(args: dict) -> str:
        try:
            r = _git_ro("status", "--short", "--branch")
        except Exception as e:  # noqa: BLE001
            return f"git status 失败: {e}（不是 git 仓库？）"
        out = (r.stdout or "").strip()
        lines = [ln for ln in out.splitlines() if ln.strip()]
        # `--branch` 总会带一行 `## <branch>`；没有文件改动行 = 干净
        files = [ln for ln in lines if not ln.startswith("##")]
        if not files:
            head = lines[0] if lines else "## (无分支)"
            return f"{head}  —— 工作区干净，无未提交改动"
        return out

    async def _list_branches(args: dict) -> str:
        """列本地分支（按最近提交排序，标注当前分支）——尤其方便看 dev_* 产出的 vorto/* 分支。"""
        try:
            r = _git_ro("for-each-ref", "--sort=-committerdate", "--count=40",
                        "--format=%(refname:short)\t%(committerdate:relative)\t%(subject)",
                        "refs/heads/")
            cur = _git_ro("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        except Exception as e:  # noqa: BLE001
            return f"git 列分支失败: {e}（不是 git 仓库？）"
        rows = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        if not rows:
            return "(没有本地分支)"
        out = ["本地分支（按最近提交排序）："]
        for ln in rows:
            parts = ln.split("\t")
            name = parts[0]
            when = parts[1] if len(parts) > 1 else ""
            subj = parts[2] if len(parts) > 2 else ""
            mark = "* " if name == cur else "  "
            out.append(f"{mark}{name}  ({when}) {subj[:60]}")
        return "\n".join(out)

    def _validated_diff_ref(value: object) -> tuple[list[str], str]:
        """Accept one verified commit-ish or two/three-dot commit range, never Git options."""
        ref = str(value or "").strip()
        if not ref:
            return [], ""
        if len(ref) > 256 or any(ch.isspace() or ord(ch) < 32 for ch in ref):
            return [], "ref 仅支持单个 revision/range，不允许空白或控制字符"
        if ref.startswith("-") or ":" in ref or "\\" in ref:
            return [], "ref 仅支持 revision、revision..revision 或 revision...revision，不允许 Git 选项/pathspec"
        if "..." in ref:
            if ref.count("...") != 1:
                return [], "ref range 格式无效"
            endpoints = ref.split("...", 1)
        elif ".." in ref:
            if ref.count("..") != 1:
                return [], "ref range 格式无效"
            endpoints = ref.split("..", 1)
        else:
            endpoints = [ref]
        if any(not endpoint for endpoint in endpoints):
            return [], "ref range 两端都必须是 revision"
        if any(endpoint.startswith("-") for endpoint in endpoints):
            return [], "ref range 端点不允许 Git 选项"
        for endpoint in endpoints:
            checked = _git_ro(
                "rev-parse", "--verify", "--quiet", "--end-of-options",
                f"{endpoint}^{{commit}}",
            )
            if checked.returncode != 0:
                return [], f"ref 含无效 revision: {endpoint[:80]}"
        return [ref], ""

    async def _show_diff(args: dict) -> str:
        ref = str(args.get("ref") or "").strip()
        try:
            extra, error = _validated_diff_ref(ref)
            if error:
                return f"git diff 出错（ref 无效）：{error}"
            stat = _git_ro("diff", "--no-ext-diff", "--no-textconv", "--stat", *extra)
            full = _git_ro("diff", "--no-ext-diff", "--no-textconv", *extra)
        except Exception as e:  # noqa: BLE001
            return f"git diff 失败: {e}"
        if stat.returncode != 0:
            return f"git diff 出错（ref 无效？）: {(stat.stderr or '').strip()[:200]}"
        diff = full.stdout or ""
        if not diff.strip():
            return f"(无改动{('：' + ref) if ref else ''})"
        body = diff[:6000] + ("\n…(diff 已截断，太长)" if len(diff) > 6000 else "")
        return f"{(stat.stdout or '').strip()}\n\n{body}"

    return [
        Tool("read_file",
             "读取仓库内某文件；给 start(/end) 读指定行段（1-based，含端点）——接 find_definition/"
             "document_symbols 给的行号跳到大文件深处；不给则整文件（超长截断、提示用行段）。"
             "图片文件（png/jpg/gif/webp/bmp）会作为图片附件注入上下文——你能直接看图"
             "（设计稿/报错截图/浏览器验证截图都能读）",
             {"path": "相对路径", "start": "可选，起始行号", "end": "可选，结束行号"},
             _read_file, read_only=True),
        Tool("list_files", "列出全仓库文本文件（py/js/html/md/yaml/toml… 跳过 .git/node_modules 等；"
             "可按子目录前缀过滤）", {"dir": "可选子目录"}, _list_files, read_only=True),
        Tool("glob", "按文件名模式找文件（不限文本扩展名、跳 .git/node_modules 等、最近修改排前）："
             "pattern 不含 / 匹配文件名（如 *.ts、*.test.js、conftest.py，任意深度），含 / 匹配相对路径"
             "（**最常用就给 *.ext）。可选 dir 限子目录",
             {"pattern": "文件名 glob，如 *.ts / **/*.py / src/**/*.css", "dir": "可选子目录"},
             _glob, read_only=True),
        Tool("grep", "在全仓库文本文件里按正则搜索（不止 src，含 web/examples/docs/配置等），"
             "返回 path:line 命中行；给 context=N 则带每处命中前后各 N 行（≤5，> 标命中行，"
             "类似 ripgrep -C，定位后不必再 read_file）",
             {"pattern": "正则", "dir": "可选子目录", "context": "可选，命中行前后各显示的行数(±N，≤5)"},
             _grep, read_only=True),
        Tool("analyze_repo", "只读扫描本仓库列出问题清单，无需 key", {}, _analyze_repo, read_only=True),
        Tool("find_definition",
             "语义查符号定义（jedi/LSP 级，跟随 import、比 grep 准）：给函数/类/变量名"
             "（可点号如 Class.method），返回定义位置+签名+文档",
             {"symbol": "符号名"}, _find_definition, read_only=True),
        Tool("find_references",
             "语义查符号的全项目引用（jedi/LSP 级）：给符号名，返回所有使用处 path:line",
             {"symbol": "符号名"}, _find_references, read_only=True),
        Tool("document_symbols",
             "列一个 .py 文件的类/函数结构大纲（jedi）：给路径，返回各定义的行号+签名，"
             "不必读全文就掌握其 API 面",
             {"path": "相对路径"}, _document_symbols, read_only=True),
        Tool("git_status", "看工作区 git 状态（git status -sb：当前分支 + 改动文件），只读",
             {}, _git_status, read_only=True),
        Tool("list_branches",
             "列本地分支（按最近提交排序、标当前分支）；尤其用来看 dev_isolated/dev_parallel/"
             "dev_auto 产出的 vorto/* 分支有哪些、各自最后提交。只读",
             {}, _list_branches, read_only=True),
        Tool("show_diff",
             "看 git diff（只读）：不给 ref 看工作区改动；给 ref 看指定范围，如 "
             "`main...vorto/x`（review dev_isolated/dev_parallel 落的分支，不必 checkout）",
             {"ref": "可选，git ref/范围，如 main...vorto/x"}, _show_diff, read_only=True),
    ]


def build_write_tools(root: str) -> list[Tool]:
    """构建写工具（edit_file/write_file），根目录限定在 root 且**无模态确认**——

    专给隔离 worktree 里的可写子 agent 用：改动只落在一次性工作树、最后整体出 diff 待人工确认，
    所以这里不逐条弹窗。带 `..` 越界防护，绝不写出 root 之外。
    """
    from pathlib import Path
    base = Path(root).resolve()

    def _safe(rel) -> Optional[Path]:
        return _resolve_within(base, rel)          # 与读工具共用同一边界防护，避免读/写漂移

    async def _edit_file(args: dict) -> str:
        rel = str(args.get("path", "")).strip()
        old, new = str(args.get("old", "")), str(args.get("new", ""))
        all_ = _truthy(args.get("replace_all", args.get("all")))
        p = _safe(rel)
        if p is None:
            return f"路径越界或非法: {rel}"
        if not old:
            return "edit_file 需要 old（要替换的原文）。"
        if not p.is_file():
            return f"文件不存在: {rel}"
        text = p.read_text(encoding="utf-8")
        cnt = text.count(old)
        if cnt == 0:
            return _edit_miss_hint(rel, text, old)
        if cnt > 1 and not all_:
            return (f"原文在 {rel} 中出现 {cnt} 次、不唯一；请给更长、唯一的 old，"
                    f"或传 replace_all=true 一次替换全部 {cnt} 处。")
        n = cnt if all_ else 1
        p.write_text(text.replace(old, new, n), encoding="utf-8")
        return f"已修改 {rel}（替换 {n} 处）。"

    async def _write_file(args: dict) -> str:
        rel = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        p = _safe(rel)
        if p is None or p.is_dir():
            return f"路径越界或非法: {rel}"
        verb = "覆盖" if p.is_file() else "新建"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已{verb} {rel}（{len(content)} 字符）。"

    return [
        Tool("edit_file", "精确字符串替换：默认 old 须唯一存在（替 1 处）；old 出现多次时传 "
             "replace_all=true 一次替换全部（批量改名/统一字面量省去逐处加上下文）。在隔离工作区改文件",
             {"path": "相对路径", "old": "要替换的原文", "new": "替换为",
              "replace_all": "可选，true=替换全部出现处（默认仅在唯一时替 1 处）"},
             _edit_file, read_only=False),
        Tool("write_file", "新建或覆盖文件；在隔离工作区改文件",
             {"path": "相对路径", "content": "文件全部内容"}, _write_file, read_only=False),
    ]


def build_test_tool(root: str, default_cmd: Optional[list] = None) -> "Tool":
    """给隔离实现子 agent 一个**受限**的 run_tests 工具：只能在 root 跑测试（不是任意 shell），

    让它 implement→test→fix 自我迭代——产出的 diff 是"已经自己跑通的"，而不是盲改后才发现没过。
    命令按仓库类型自动探测（pytest/npm/go/cargo/make），不再写死 pytest；autonomous 也只跑测试、不乱执行。
    """
    async def _handler(args: dict) -> str:
        import asyncio
        from src.agents.test_detect import detect_test_cmd, is_pytest_cmd
        from src.agents.worktree import run_tests
        from src.utils.python_exe import pytest_argv
        base = default_cmd or detect_test_cmd(root)
        sel = str(args.get("test") or "").strip()
        # selector 只对 pytest 有意义（文件级 narrow）；非 pytest 命令忽略 selector、跑整套
        if sel and is_pytest_cmd(base):
            cmd = pytest_argv("-q", sel)
        else:
            cmd = list(base)
        res = await asyncio.to_thread(run_tests, root, cmd)
        tag = "通过 ✓" if res["ok"] else "未过 ✗"
        return f"测试{tag}（{res['cmd']}）。输出尾部：\n{res['output'][-2500:]}"

    return Tool("run_tests",
                "在当前隔离工作区跑测试自测（命令按仓库类型自动探测；pytest 可传 test 选择器 narrow，"
                "省略/非 pytest 跑整套）；实现后务必自测，没过就改完再测，直到通过",
                {"test": "可选，pytest 文件级选择器，如 tests/unit/test_x.py（仅 pytest 生效）"},
                _handler, read_only=True, required_capabilities=("host_process",))


def build_confirmed_write_tools(root: str, confirm, on_diff=None) -> list["Tool"]:
    """主工作区里的直接编辑（edit_file/write_file）：**每次写盘前过确认门，并先给 diff**。

    为什么要有这一档（2026-09-17 真机诊断）：Desktop/Web/CLI 共用的工具集此前**没有任何直写
    工具**，写只能走隔离 dev 流水线。于是"给 TodoList 加个 3 行方法"这种改动也要开 worktree +
    自测，跑一趟几分钟；流水线一旦受挫，模型就退而求其次去 `run_command` 里 `sed -i`/`python -c`
    拼脚本改文件——既难看懂，也更容易改坏（真机上 macOS 的 `sed -i` 语法不同，静默改了个寂寞）。

    与 `build_write_tools`（无确认）的分工：那一档只给**一次性 worktree 里的子 agent**，
    改动落在临时工作树、最后整体出 diff；这一档直接改用户的工作区，所以逐次确认，
    且确认文案里带 unified diff——人要看见改什么，而不是只看见一个文件名。

    确认门申报 ``kind=write``：授权档位为"完全信任"时免确认（见 agents/trust.py），
    但污点回合一律回到真人拍板——这条在内核 `decide()` 里，这里绕不过去。
    """
    import difflib
    from pathlib import Path

    from src.agents.gate import request
    from src.agents.trust import WRITE

    base = Path(root).resolve()
    _MAX_DIFF_LINES = 200

    def _safe(rel) -> Optional[Path]:
        return _resolve_within(base, rel)

    def _diff(rel: str, before: str, after: str) -> str:
        lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                          fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""))
        if len(lines) > _MAX_DIFF_LINES:
            omitted = len(lines) - _MAX_DIFF_LINES
            lines = lines[:_MAX_DIFF_LINES] + [f"… 还有 {omitted} 行未显示"]
        return "\n".join(lines)

    async def _approve(rel: str, before: str, after: str, verb: str) -> tuple[bool, str]:
        diff = _diff(rel, before, after)
        if on_diff is not None:
            try:
                on_diff(rel, diff)                 # 端可以把 diff 渲染到界面（TUI 着色 / Desktop 面板）
            except Exception:  # noqa: BLE001 —— 展示失败不该影响判定
                pass
        ok = await request(confirm, f"{verb} {rel}？\n{diff}", WRITE)
        return ok, diff

    async def _edit_file(args: dict) -> str:
        rel = str(args.get("path", "")).strip()
        old, new = str(args.get("old", "")), str(args.get("new", ""))
        all_ = _truthy(args.get("replace_all", args.get("all")))
        p = _safe(rel)
        if p is None:
            return f"路径越界或非法: {rel}"
        if not old:
            return "edit_file 需要 old（要替换的原文）。"
        if not p.is_file():
            return f"文件不存在: {rel}"
        text = p.read_text(encoding="utf-8")
        cnt = text.count(old)
        if cnt == 0:
            return _edit_miss_hint(rel, text, old)
        if cnt > 1 and not all_:
            return (f"原文在 {rel} 中出现 {cnt} 次、不唯一；请给更长、唯一的 old，"
                    f"或传 replace_all=true 一次替换全部 {cnt} 处。")
        n = cnt if all_ else 1
        after = text.replace(old, new, n)
        if after == text:
            return f"{rel} 内容没有变化，未写盘。"
        ok, _ = await _approve(rel, text, after, "修改")
        if not ok:
            return f"用户取消了对 {rel} 的修改；未写盘。"
        p.write_text(after, encoding="utf-8")
        return f"已修改 {rel}（替换 {n} 处）。"

    async def _write_file(args: dict) -> str:
        rel = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        p = _safe(rel)
        if p is None or p.is_dir():
            return f"路径越界或非法: {rel}"
        exists = p.is_file()
        before = p.read_text(encoding="utf-8") if exists else ""
        if exists and before == content:
            return f"{rel} 内容没有变化，未写盘。"
        ok, _ = await _approve(rel, before, content, "覆盖" if exists else "新建")
        if not ok:
            return f"用户取消了对 {rel} 的写入；未写盘。"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已{'覆盖' if exists else '新建'} {rel}（{len(content)} 字符）。"

    return [
        Tool("edit_file", "精确字符串替换（默认 old 须唯一；多处传 replace_all=true）。"
             "直接改当前工作区的文件，写盘前会把 diff 交给人确认",
             {"path": "相对路径", "old": "要替换的原文", "new": "替换为",
              "replace_all": "可选，true=替换全部出现处（默认仅在唯一时替 1 处）"},
             _edit_file, read_only=False),
        Tool("write_file", "新建或覆盖当前工作区的文件；写盘前会把 diff 交给人确认",
             {"path": "相对路径", "content": "文件全部内容"}, _write_file, read_only=False),
    ]
