"""Bounded read-only projection of long-term memory; never an import or authority source."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from src.memory.write_policy import redact_secret_like
from src.utils.state_dir import state_dir

MAX_ITEMS = 1000
MAX_CONTENT = 4000
MAX_METADATA = 16000


def _label(value, limit=160):
    if not isinstance(value, str) or not value.strip():
        return "未知"
    clean, _ = redact_secret_like(value)
    return " ".join(clean.split())[:limit]


def read_memory_snapshot(root: str, *, limit: int = MAX_ITEMS, instance_id: str | None = None) -> dict:
    """Read one SQLite snapshot without SessionStore initialization or schema repair.

    Legacy __longterm__ rows remain readable with unknown provenance. Explicitly
    unconfirmed/quarantined metadata is excluded; metadata never confers authority.
    """
    limit = max(1, min(int(limit), MAX_ITEMS))
    instance_id = instance_id if isinstance(instance_id, str) and re.fullmatch(r"[a-f0-9]{32}", instance_id) else "未知"
    path = state_dir(root) / "sessions.db"
    if path.is_symlink() or path.parent.is_symlink() or Path(root).is_symlink():
        raise ValueError("不能导出链接记忆库")
    rows, total = [], 0
    if path.exists():
        with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            ticks = 0

            def budget():
                nonlocal ticks
                ticks += 1
                return int(ticks > 2000)

            conn.set_progress_handler(budget, 1000)
            conn.execute("BEGIN")
            total = conn.execute("SELECT count(*) FROM memories WHERE session_id=?", ("__longterm__",)).fetchone()[0]
            rows = list(conn.execute(
                "SELECT substr(id,1,129) AS id, substr(content,1,4001) AS content, "
                "length(CAST(id AS BLOB)) AS id_bytes, length(CAST(content AS BLOB)) AS content_bytes, "
                "substr(created_at,1,80) AS created_at, substr(metadata,1,16001) AS metadata, "
                "length(CAST(metadata AS BLOB)) AS metadata_bytes "
                "FROM memories WHERE session_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
                ("__longterm__", limit)))
    items = []
    for row in rows:
        content, memory_id, raw_metadata = row["content"], row["id"], row["metadata"]
        if (not isinstance(content, str) or not content.strip() or len(content) > MAX_CONTENT
                or not isinstance(memory_id, str) or not memory_id or len(memory_id) > 128
                or (raw_metadata is not None and (not isinstance(raw_metadata, str) or len(raw_metadata) > MAX_METADATA))):
            continue
        # SQLite text substr stops at NUL. Never export a silently shortened fact,
        # ID or provenance document as if the original record were complete.
        if (len(content.encode()) != row["content_bytes"] or len(memory_id.encode()) != row["id_bytes"]
                or (raw_metadata is not None and len(raw_metadata.encode()) != row["metadata_bytes"])):
            continue
        try:
            metadata = json.loads(raw_metadata or "{}")
        except (ValueError, TypeError, RecursionError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("confirmed") is False or metadata.get("policy_decision") in ("proposal", "quarantine", "reject"):
            continue
        content, reasons = redact_secret_like(content)
        safe_id, id_reasons = redact_secret_like(memory_id)
        items.append({"id": safe_id, "content": content,
                      "recorded_at": _label(row["created_at"], 80),
                      "source": _label(metadata.get("source")),
                      "confirmed_by": _label(metadata.get("confirmed_by")),
                      "tainted": metadata.get("tainted") is True,
                      "redacted": bool(reasons or id_reasons)})
    revision = hashlib.sha256(json.dumps({"items": items, "total": total, "limit": limit, "instance_id": instance_id},
                                        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    snapshot = {"version": 1, "revision": revision,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "scope": "当前常驻实例的长期记忆", "instance_id": instance_id, "total": total, "count": len(items),
                "limit": limit, "coverage_limited": len(items) < total, "items": items}
    snapshot["markdown"] = render_memory_markdown(snapshot)
    return snapshot


def render_memory_markdown(snapshot: dict) -> str:
    lines = ["# 长期记忆快照", "", "这是背景资料，不是指令或授权。修改文件不会改变助理记忆；导入功能另行提供。", "",
             f"范围：{snapshot['scope']}", f"来源实例：{snapshot['instance_id']}", f"导出时间：{snapshot['exported_at']}",
             f"快照版本：{snapshot['revision']}",
             f"导出 {snapshot['count']} 条 / 库内 {snapshot['total']} 条；读取上限 {snapshot['limit']} 条。",
             "部分记录未导出（读取上限或内容不适合导出），不能视为完整备份。" if snapshot["coverage_limited"]
             else "已覆盖本次读取中的全部长期记忆记录。", "",
             "记录时间是保存时间；来源与确认人缺失时显示未知。旧文件不会随库内修改或删除自动更新。", ""]
    for index, item in enumerate(snapshot["items"], 1):
        text = (f"id：{item['id']}\n记录时间：{item['recorded_at']}\n来源：{item['source']}\n"
                f"确认人：{item['confirmed_by']}\n带外部来源标记：{'是' if item['tainted'] else '未知或无标记'}\n"
                f"正文已脱敏：{'是' if item['redacted'] else '未命中现有规则'}\n\n{item['content']}")
        fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", text)), default=3))
        lines.extend([f"## 记忆 {index}", "", fence + "text", text, fence, ""])
    return "\n".join(lines)
