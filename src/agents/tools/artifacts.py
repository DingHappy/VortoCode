"""Artifacts（制品）—— 把会话产出的自包含 HTML 发布成可分享、可实时更新的网页。

复刻 Claude Code 的 artifact 能力，落到 VortoCode 的本地优先 / 人在关口模型：
- 制品存到 `.vortocode/artifacts/<id>/`（gitignored，纯运行时产物，不进版本库）；
- 由 Web 服务器在 `/artifact/<id>` 渲染（iframe 沙箱 + 限制性 CSP，禁止外联——
  对齐 CC「静态、无外部请求」的隔离：制品脚本可跑但不能联网/SSRF）；
- 页面随会话**实时更新**：重发同一 id 会 version+1，查看页轮询版本号自动刷新；
- 发布是**写操作**：TUI 首次发布弹确认（之后静默更新），Web 端靠 build 模式门控——都落「人在关口」。

本模块只管"存储 + 工具封装"，**零 Web 依赖**；HTTP 路由见 `src/web/routers/artifacts.py`。
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional


def _slugify(title: str) -> str:
    """标题 → ASCII kebab（id 段恒为 ASCII，避免 URL/路径里出现 CJK）。空则 'artifact'。"""
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower())
    return s.strip("-")[:40] or "artifact"


# 制品体积上限：默认 16 MiB（对齐 Claude Code），VORTOCODE_ARTIFACT_MAX_BYTES 可覆盖。
DEFAULT_MAX_BYTES = 16 * 1024 * 1024


def max_bytes() -> int:
    try:
        v = int(os.getenv("VORTOCODE_ARTIFACT_MAX_BYTES") or DEFAULT_MAX_BYTES)
        return v if v > 0 else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


# 渲染 markdown 制品用的整页外壳（GitHub 风排版，内联 CSS，无外联）。
_MD_DOC = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>__TITLE__</title>
<style>
  body { max-width: 860px; margin: 0 auto; padding: 40px 24px; line-height: 1.7;
         font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         color: #1f2328; background: #fff; }
  h1, h2, h3 { line-height: 1.25; margin: 1.4em 0 .6em; }
  h1, h2 { border-bottom: 1px solid #d8dee4; padding-bottom: .3em; }
  code { background: #eff1f3; padding: .15em .4em; border-radius: 5px; font-size: 92%; }
  pre { background: #f6f8fa; padding: 14px; border-radius: 8px; overflow: auto; }
  pre code { background: none; padding: 0; }
  blockquote { margin: 0; padding: 0 1em; color: #57606a; border-left: .25em solid #d0d7de; }
  table { border-collapse: collapse; } th, td { border: 1px solid #d0d7de; padding: 6px 13px; }
  a { color: #0969da; } img { max-width: 100%; }
</style></head><body>
__BODY__
</body></html>"""


def render_markdown_doc(title: str, md_text: str) -> str:
    """markdown → 自包含 HTML 整页。无 markdown 库时优雅降级为 <pre>。"""
    import html as _h
    try:
        import markdown as _md
        body = _md.markdown(md_text or "", extensions=["fenced_code", "tables", "sane_lists"])
    except Exception:  # noqa: BLE001
        body = "<pre>" + _h.escape(md_text or "") + "</pre>"
    return _MD_DOC.replace("__TITLE__", _h.escape(title or "制品")).replace("__BODY__", body)


class ArtifactStore:
    """文件系统制品库：`.vortocode/artifacts/<id>/{index.html, meta.json}`。"""

    _ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")

    def __init__(self, repo_root: str) -> None:
        self.root = Path(repo_root) / ".vortocode" / "artifacts"

    @classmethod
    def _safe_id(cls, artifact_id: str) -> Optional[str]:
        """制品 id 必须是单段 [A-Za-z0-9_-]，挡掉 ../、/ 等路径穿越。非法返回 None。"""
        aid = (artifact_id or "").strip()
        return aid if cls._ID_RE.fullmatch(aid) else None

    def _dir(self, artifact_id: str) -> Path:
        return self.root / artifact_id

    def exists(self, artifact_id: str) -> bool:
        aid = self._safe_id(artifact_id)
        return bool(aid and (self._dir(aid) / "meta.json").is_file())

    def publish(self, title: str, html: str, artifact_id: Optional[str] = None,
                kind: str = "html") -> dict:
        """写入/更新一个制品并返回 meta。传已存在的 id 即原地更新（version+1，保留 created_at）。

        超过体积上限（见 max_bytes，默认 16 MiB）抛 ValueError —— 对齐 CC 的大小约束。
        """
        data = (html or "").encode("utf-8")
        cap = max_bytes()
        if len(data) > cap:
            raise ValueError(f"制品体积 {len(data)} 字节超过上限 {cap} 字节"
                             f"（{cap // (1024 * 1024)} MiB）")
        now = datetime.now().isoformat(timespec="seconds")
        aid = self._safe_id(artifact_id) if artifact_id else None
        if not aid:
            aid = f"{_slugify(title)}-{uuid.uuid4().hex[:6]}"
        d = self._dir(aid)
        d.mkdir(parents=True, exist_ok=True)
        meta_path = d / "meta.json"
        version, created, versions = 1, now, []
        if meta_path.is_file():
            try:
                old = json.loads(meta_path.read_text(encoding="utf-8"))
                version = int(old.get("version", 0)) + 1
                created = old.get("created_at", now)
                versions = list(old.get("versions") or [])
            except Exception:  # noqa: BLE001
                pass
        (d / f"v{version}.html").write_text(html or "", encoding="utf-8")   # 版本快照（可回看）
        (d / "index.html").write_text(html or "", encoding="utf-8")          # 当前=最新
        versions.append({"v": version, "ts": now, "bytes": len(data)})
        meta = {
            "id": aid,
            "title": (title or aid).strip(),
            "kind": kind,
            "version": version,
            "pinned": None,                  # 发布即展示最新（清除 pin）—— 对齐 CC「选给查看者看哪版」
            "created_at": created,
            "updated_at": now,
            "bytes": len(data),
            "versions": versions,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def delete(self, artifact_id: str) -> bool:
        """删除一个制品（整目录）。成功 True；id 非法或不存在 False。"""
        aid = self._safe_id(artifact_id)
        if not aid:
            return False
        d = self._dir(aid)
        if not d.is_dir():
            return False
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        return True

    def meta(self, artifact_id: str) -> Optional[dict]:
        aid = self._safe_id(artifact_id)
        if not aid:
            return None
        p = self._dir(aid) / "meta.json"
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def html(self, artifact_id: str, version: Optional[int] = None) -> Optional[str]:
        """取制品 HTML。version 给定取该版本快照（缺失则回退当前）；否则取当前(index.html)。"""
        aid = self._safe_id(artifact_id)
        if not aid:
            return None
        d = self._dir(aid)
        if version is not None:
            try:
                vp = d / f"v{int(version)}.html"
            except (TypeError, ValueError):
                vp = None
            if vp and vp.is_file():
                return vp.read_text(encoding="utf-8")
        p = d / "index.html"
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def versions(self, artifact_id: str) -> list[dict]:
        """版本清单 [{v, ts, bytes}]，升序。老 meta 无记录则按当前版本合成一条。"""
        m = self.meta(artifact_id)
        if not m:
            return []
        vs = m.get("versions")
        if vs:
            return sorted(vs, key=lambda x: int(x.get("v", 0)))
        return [{"v": int(m.get("version", 1)), "ts": m.get("updated_at", ""), "bytes": m.get("bytes", 0)}]

    def pin(self, artifact_id: str, version: Optional[int]) -> Optional[dict]:
        """把"当前"指向某个历史版本（CC：选给查看者看哪一版）。version=None 取消 pin、回到最新。

        返回更新后的 meta；id/版本非法返回 None。
        """
        aid = self._safe_id(artifact_id)
        if not aid:
            return None
        m = self.meta(aid)
        if not m:
            return None
        d = self._dir(aid)
        if version is None:                                   # 取消 pin → 当前 = 最新
            target = int(m.get("version", 1))
            m["pinned"] = None
        else:
            try:
                target = int(version)
            except (TypeError, ValueError):
                return None
            if target not in {int(x["v"]) for x in self.versions(aid)}:
                return None
            m["pinned"] = target
        content = self.html(aid, target)
        if content is None:
            return None
        (d / "index.html").write_text(content, encoding="utf-8")   # 当前内容指向目标版本
        (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        return m

    def list(self) -> list[dict]:
        """所有制品 meta，按 updated_at 倒序。"""
        if not self.root.is_dir():
            return []
        out: list[dict] = []
        for d in sorted(self.root.iterdir()):
            if d.is_dir():
                m = self.meta(d.name)
                if m:
                    out.append(m)
        out.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        return out


def artifact_url(base_url: Optional[str], artifact_id: str) -> str:
    """拼可点链接。base_url 缺省读 VORTOCODE_WEB_BASE，再缺省 http://127.0.0.1:8080。"""
    base = (base_url or os.getenv("VORTOCODE_WEB_BASE") or "http://127.0.0.1:8080").rstrip("/")
    return f"{base}/artifact/{artifact_id}"


def build_artifact_tools(
    repo_root: str,
    confirm: Optional[Callable[[dict, bool], Awaitable[bool]]] = None,
    base_url: Optional[str] = None,
    on_published: Optional[Callable[[dict, str], None]] = None,
    confirm_delete: Optional[Callable[[dict], Awaitable[bool]]] = None,
) -> list:
    """构建 publish_artifact / list_artifacts / delete_artifact 工具（TUI 与 Web 共用）。

    - confirm(preview, is_update) -> bool：发布前确认钩子（TUI 接 ConfirmScreen）。**何时调用由 _publish
      统一决定**（不由回调自己判）：首次发布必问；之后更新静默（对齐 CC「批准后再发不再问」），
      **但污点回合的更新仍要问**（防"先发无害、再诱导 update 成恶意页"绕过确认）。回调只管"问不问得到人"。
    - confirm_delete(preview) -> bool：删除前确认钩子（同理，TUI 接 ConfirmScreen）。
    - base_url：拼链接用（见 artifact_url）。
    - on_published(meta, url)：发布成功回调，供 UI 提示链接。
    """
    from src.agents.tool import Tool

    store = ArtifactStore(repo_root)

    async def _publish(args: dict) -> str:
        title = str(args.get("title", "")).strip()
        html = str(args.get("html", "") or "")
        md = str(args.get("markdown") or args.get("md") or "")
        artifact_id = str(args.get("id") or args.get("artifact_id") or "").strip() or None
        kind = "html"
        if not html.strip() and md.strip():     # 给了 markdown → 服务端渲染成自包含整页
            html = render_markdown_doc(title, md)
            kind = "markdown"
        if not html.strip():
            return ("publish_artifact 需要 html 或 markdown（二选一；自包含、内联 CSS/JS、"
                    "不要引用外部资源，否则会被 CSP 拦截）。")
        if not title and not artifact_id:
            return "publish_artifact 需要 title（页面标题）。"
        is_update = bool(artifact_id and store.exists(artifact_id))
        # 确认门策略**统一在这里**（工具边界），不由各端的 confirm 回调各自实现——否则加一端漏一端。
        # 既有约定：首次发布问一次，之后更新静默（对齐 CC「批准后再发不再问」）。
        # **但污点回合下连更新也必须过门**：否则"先发一版无害的、再借外部内容诱导 update 成恶意页"
        # 就完全绕过确认、静默覆盖已发布页面。此前 `not is_update` 把污点更新也一并跳过了——
        # 内核适配器 _art_publish 里那句"污点更新要过门"因此是**够不到的死代码**（自审逮到的真洞）。
        from src.agents.taint import is_tainted
        if confirm is not None and (not is_update or is_tainted()):
            try:
                ok = await confirm({"title": title, "id": artifact_id}, is_update)
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                return "用户取消了发布制品。"
        try:
            meta = store.publish(title or (artifact_id or ""), html, artifact_id, kind=kind)
        except ValueError as e:
            return f"发布失败：{e}。请精简内容，或调大环境变量 VORTOCODE_ARTIFACT_MAX_BYTES。"
        url = artifact_url(base_url, meta["id"])
        if on_published is not None:
            try:
                on_published(meta, url)
            except Exception:  # noqa: BLE001
                pass
        verb = "更新" if meta["version"] > 1 else "发布"
        return (f"已{verb}制品「{meta['title']}」(id={meta['id']}, v{meta['version']}, {kind})。\n"
                f"查看：{url}\n"
                f"（要更新同一页面，下次带 id=\"{meta['id']}\" 重新调用即可，已打开的页面会自动刷新。）")

    async def _list(_args: dict) -> str:
        items = store.list()
        if not items:
            return "（还没有制品。用 publish_artifact 发布一个自包含 HTML 页面。）"
        lines = [
            f"- {m['title']} (id={m['id']}, v{m['version']}, {m.get('kind', 'html')}, "
            f"{m['updated_at']}) → {artifact_url(base_url, m['id'])}"
            for m in items
        ]
        return "制品列表:\n" + "\n".join(lines)

    async def _delete(args: dict) -> str:
        aid = str(args.get("id") or args.get("artifact_id") or "").strip()
        if not aid:
            return "delete_artifact 需要 id（先 list_artifacts 看 id）。"
        if not store.exists(aid):
            return f"没有 id={aid} 的制品。"
        if confirm_delete is not None:
            try:
                ok = await confirm_delete({"id": aid})
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                return f"用户取消了删除 {aid}。"
        return f"已删除制品 {aid}。" if store.delete(aid) else f"删除 {aid} 失败。"

    return [
        Tool(
            "publish_artifact",
            "把自包含网页内容发布成可分享、随会话实时更新的网页（制品）；写操作，仅 build。"
            "传 html 或 markdown（二选一，markdown 会渲染成整页）。传相同 id 则原地更新、已打开的页面会自动刷新。"
            "适合：带注释的 PR 走查、数据看板、方案对比、交互控件（滑块/开关）、迁移/排查进度清单、报告。",
            {"title": "页面标题", "html": "完整自包含 HTML（内联 CSS/JS，勿引用外部资源）",
             "markdown": "或：markdown 文本（与 html 二选一）", "id": "可选；更新已有制品时传它的 id"},
            _publish, read_only=False,
        ),
        Tool(
            "list_artifacts", "列出已发布的制品（标题/id/版本/类型/链接）",
            {}, _list, read_only=True,
        ),
        Tool(
            "delete_artifact", "删除一个制品；写操作，需确认，仅 build",
            {"id": "要删除的制品 id"}, _delete, read_only=False,
        ),
    ]
