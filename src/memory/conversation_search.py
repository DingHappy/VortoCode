"""Local lexical recall over SessionStore conversation records, separate from facts.

Index contents remain untrusted conversation data. This backend does not promote
historical instructions into long-term facts or expose an HTTP import endpoint.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

_STOP = set("a an the i me my you your we our what which who when where why how is are was were do did does have has had can could would should to of in on at for with and or it that this as from about tell please all any many much last first".split())


class ConversationSearch:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def search(self, query: str, *, limit: int = 12, neighbors: int = 1,
               diversify: bool = False) -> list[dict]:
        if not self.db_path.exists():
            return []
        terms = list(dict.fromkeys(word for word in re.findall(r"[^\W_]+", query[:2000].lower())
                                   if word not in _STOP and len(word) > 1))[:32]
        if not terms:
            return []
        match = " OR ".join('"' + word + '"' for word in terms)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            existed = conn.execute("SELECT 1 FROM sqlite_master WHERE name='conversation_fts'").fetchone()
            if not existed:
                conn.executescript('''
                    CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5(content, content='messages', content_rowid='rowid');
                    CREATE TRIGGER IF NOT EXISTS conversation_ai AFTER INSERT ON messages BEGIN
                        INSERT INTO conversation_fts(rowid, content) VALUES (new.rowid, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS conversation_ad AFTER DELETE ON messages BEGIN
                        INSERT INTO conversation_fts(conversation_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS conversation_au AFTER UPDATE OF content ON messages BEGIN
                        INSERT INTO conversation_fts(conversation_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
                        INSERT INTO conversation_fts(rowid, content) VALUES (new.rowid, new.content);
                    END;
                ''')
                conn.execute("INSERT INTO conversation_fts(conversation_fts) VALUES ('rebuild')")
            count = max(1, min(limit, 50))
            hits = conn.execute("SELECT m.rowid AS row_number, m.session_id FROM conversation_fts f JOIN messages m ON m.rowid=f.rowid WHERE conversation_fts MATCH ? ORDER BY bm25(conversation_fts), m.rowid LIMIT ?", (match, count * 16 if diversify else count)).fetchall()
            if diversify:
                # One strong hit per conversation before taking repeated hits from
                # the same conversation. Ranking remains lexical and label-free.
                groups = {}
                for hit in hits:
                    groups.setdefault(hit['session_id'], []).append(hit)
                hits = []
                depth = 0
                while len(hits) < count:
                    batch = [group[depth] for group in groups.values() if len(group) > depth]
                    if not batch:
                        break
                    hits.extend(batch[:count - len(hits)])
                    depth += 1
            hit_numbers = {hit['row_number'] for hit in hits}
            seen, results, session_rows = set(), [], {}
            for hit in hits:
                if hit['session_id'] not in session_rows:
                    session_rows[hit['session_id']] = conn.execute("SELECT rowid AS row_number, * FROM messages WHERE session_id=? ORDER BY rowid", (hit['session_id'],)).fetchall()
                rows = session_rows[hit['session_id']]
                index = next(i for i, row in enumerate(rows) if row['row_number'] == hit['row_number'])
                for row in rows[max(0, index - min(max(neighbors, 0), 2)):index + min(max(neighbors, 0), 2) + 1]:
                    if row['id'] in seen:
                        continue
                    seen.add(row['id'])
                    metadata = json.loads(row['metadata'] or '{}')
                    results.append({"id": row['id'], "session_id": row['session_id'], "role": row['role'],
                                    "content": row['content'], "date": metadata.get('history_date') or row['created_at'],
                                    "source_session": metadata.get('source_session') or row['session_id'],
                                    "position": row['row_number'], "is_hit": row['row_number'] in hit_numbers})
            return results
