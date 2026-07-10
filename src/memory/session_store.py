"""会话持久化 - SQLite 存储"""

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionStore:
    """会话存储"""
    
    def __init__(self, db_path: str = ".vortocode/sessions.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """初始化数据库"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    status TEXT DEFAULT 'active',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    created_at TEXT,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    title TEXT,
                    description TEXT,
                    status TEXT DEFAULT 'pending',
                    agent_id TEXT,
                    result TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS edits (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    file TEXT,
                    old_content TEXT,
                    new_content TEXT,
                    applied BOOLEAN DEFAULT 0,
                    created_at TEXT,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    type TEXT,
                    content TEXT,
                    importance REAL DEFAULT 0.5,
                    created_at TEXT,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)

            # 不可信/敏感内容先进入独立提案区，绝不混进正常 recall 的 memories 表。
            # 这是纯加法迁移：旧 sessions.db 无需 ALTER，打开时自动补表。
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_proposals (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reasons TEXT DEFAULT '[]',
                    source TEXT NOT NULL,
                    origin_session_id TEXT,
                    tainted BOOLEAN DEFAULT 0,
                    write_method TEXT NOT NULL,
                    memory_type TEXT DEFAULT 'fact',
                    importance REAL DEFAULT 0.5,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT,
                    reviewed_at TEXT,
                    reviewer TEXT,
                    memory_id TEXT,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            
            # 创建索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edits_session ON edits(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_proposals_status "
                         "ON memory_proposals(status, created_at)")
    
    def create_session(self, name: str = None) -> str:
        """创建会话"""
        session_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO sessions (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, name or f"Session {session_id}", now, now)
            )
        
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cursor.fetchone()
            
            if row:
                return dict(row)
        return None
    
    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列出会话"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def search_sessions(self, query: str, limit: int = 30) -> List[Dict[str, Any]]:
        """搜索历史会话：匹配 session id/name/metadata 摘要和消息正文。

        `/sessions` 选择器只看最近会话；这里给跨历史的轻量搜索。返回每个 session 一行，并附带
        `match_count` 与 `match_snippet`，方便 UI 展示为什么命中。
        """
        q = (query or "").strip().lower()
        if not q:
            return self.list_sessions(limit)
        like = f"%{q}%"
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT
                    s.*,
                    COUNT(m.id) AS match_count,
                    MAX(CASE WHEN lower(m.content) LIKE ? THEN substr(m.content, 1, 180) ELSE '' END) AS match_snippet
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id AND lower(m.content) LIKE ?
                WHERE lower(s.id) LIKE ?
                   OR lower(COALESCE(s.name, '')) LIKE ?
                   OR lower(COALESCE(s.metadata, '')) LIKE ?
                   OR m.id IS NOT NULL
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (like, like, like, like, like, int(limit)),
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def update_session(self, session_id: str, **kwargs) -> bool:
        """更新会话"""
        if not kwargs:
            return False
        
        kwargs['updated_at'] = datetime.now().isoformat()
        
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [session_id]
        
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                f"UPDATE sessions SET {set_clause} WHERE id = ?",
                values
            )
        return True
    
    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM tasks WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM edits WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM memories WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return True
    
    def add_message(self, session_id: str, role: str, content: str, metadata: Dict = None) -> str:
        """添加消息"""
        message_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, session_id, role, content, now, json.dumps(metadata or {}))
            )
        
        return message_id
    
    def get_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取消息"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
                (session_id, limit)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def add_task(self, session_id: str, title: str, description: str = "", 
                 agent_id: str = None, metadata: Dict = None) -> str:
        """添加任务"""
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO tasks (id, session_id, title, description, agent_id, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, session_id, title, description, agent_id, now, now, json.dumps(metadata or {}))
            )
        
        return task_id
    
    def update_task(self, task_id: str, **kwargs) -> bool:
        """更新任务"""
        if not kwargs:
            return False
        
        kwargs['updated_at'] = datetime.now().isoformat()
        
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [task_id]
        
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = ?",
                values
            )
        return True
    
    def get_tasks(self, session_id: str, status: str = None) -> List[Dict[str, Any]]:
        """获取任务"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            
            if status:
                cursor = conn.execute(
                    "SELECT * FROM tasks WHERE session_id = ? AND status = ? ORDER BY created_at ASC",
                    (session_id, status)
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at ASC",
                    (session_id,)
                )
            
            return [dict(row) for row in cursor.fetchall()]
    
    def add_edit(self, session_id: str, file: str, old_content: str, 
                 new_content: str, metadata: Dict = None) -> str:
        """添加编辑"""
        edit_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO edits (id, session_id, file, old_content, new_content, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (edit_id, session_id, file, old_content, new_content, now, json.dumps(metadata or {}))
            )
        
        return edit_id
    
    def apply_edit(self, edit_id: str) -> bool:
        """标记编辑为已应用"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("UPDATE edits SET applied = 1 WHERE id = ?", (edit_id,))
        return True
    
    def get_edits(self, session_id: str, applied: bool = None) -> List[Dict[str, Any]]:
        """获取编辑"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            
            if applied is not None:
                cursor = conn.execute(
                    "SELECT * FROM edits WHERE session_id = ? AND applied = ? ORDER BY created_at ASC",
                    (session_id, int(applied))
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM edits WHERE session_id = ? ORDER BY created_at ASC",
                    (session_id,)
                )
            
            return [dict(row) for row in cursor.fetchall()]
    
    def add_memory(self, session_id: str, memory_type: str, content: str,
                   importance: float = 0.5, metadata: Dict = None) -> str:
        """添加记忆"""
        memory_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO memories (id, session_id, type, content, importance, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (memory_id, session_id, memory_type, content, importance, now, json.dumps(metadata or {}))
            )
        
        return memory_id
    
    def get_memories(self, session_id: str, memory_type: str = None, 
                     min_importance: float = 0.0) -> List[Dict[str, Any]]:
        """获取记忆"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            
            conditions = ["session_id = ?", "importance >= ?"]
            params = [session_id, min_importance]
            
            if memory_type:
                conditions.append("type = ?")
                params.append(memory_type)
            
            where_clause = " AND ".join(conditions)
            
            cursor = conn.execute(
                f"SELECT * FROM memories WHERE {where_clause} ORDER BY importance DESC, created_at DESC",
                params
            )
            
            return [dict(row) for row in cursor.fetchall()]
    
    def search_memories(self, session_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索记忆：**按查询词重叠度排序**，而非旧的 `LIKE '%整句%'`。

        旧版要求整条 query 作为**子串**原样出现，极脆——问「测试命令」而记忆写「跑测试用 pytest」
        就完全不匹配。这里把 query 切成词，按命中词数打分（大小写无关）、整句命中额外加权，
        再以既有的 importance/recency 次序稳定收尾。零依赖、召回大幅变准（语义向量召回留作后续）。
        """
        rows = self.get_memories(session_id)          # 已按 importance DESC, created_at DESC 排好
        q = (query or "").strip().lower()
        if not q:
            return rows[:limit]
        # 中英混排都能切：按空白 + 常见标点分词；退化时用整句
        terms = [t for t in re.split(r"[\s,，。;；、/：:()（）\[\]{}]+", q) if t] or [q]
        # 中文无空格，整词子串匹配几乎必失（问「测试怎么跑」配不上「跑测试用 pytest」）。补 CJK 相邻
        # 二元组（测试/试怎/怎么/么跑…）捕捉「测试」这类核心词——经典轻量中文匹配，零依赖。
        for run in re.findall(r"[㐀-鿿]{2,}", q):
            terms.extend(run[i:i + 2] for i in range(len(run) - 1))
        terms = list(dict.fromkeys(terms))            # 去重，避免同词重复计分
        scored = []
        for r in rows:
            content = str(r.get("content", "")).lower()
            score = sum(1 for t in terms if t in content)
            if q in content:                          # 整句原样命中额外加权（保留旧行为的强信号）
                score += len(terms)
            if score > 0:
                scored.append((score, r))
        if not scored:
            return []
        scored.sort(key=lambda sr: sr[0], reverse=True)   # 稳定排序：同分保留 importance/recency 次序
        return [r for _, r in scored][:limit]

    def add_memory_proposal(self, content: str, *, decision: str, reasons: List[str],
                            source: str, origin_session_id: str, tainted: bool,
                            write_method: str, memory_type: str = "fact",
                            importance: float = 0.5, status: str = "pending",
                            metadata: Dict = None) -> str:
        """写入隔离的记忆提案；该表不会被 get/search_memories 召回。"""
        proposal_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO memory_proposals
                    (id, content, decision, reasons, source, origin_session_id,
                     tainted, write_method, memory_type, importance, status,
                     created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (proposal_id, content, decision, json.dumps(reasons, ensure_ascii=False),
                 source, origin_session_id, int(tainted), write_method, memory_type,
                 importance, status, now, json.dumps(metadata or {}, ensure_ascii=False)),
            )
        return proposal_id

    def get_memory_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM memory_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_memory_proposals(self, status: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """列出待审/隔离记录；status=open 同时包含 pending 与 quarantined。"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if status == "open":
                cur = conn.execute(
                    """SELECT * FROM memory_proposals
                       WHERE status IN ('pending', 'quarantined')
                       ORDER BY created_at DESC LIMIT ?""",
                    (int(limit),),
                )
            elif status:
                cur = conn.execute(
                    """SELECT * FROM memory_proposals WHERE status = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (status, int(limit)),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM memory_proposals ORDER BY created_at DESC LIMIT ?",
                    (int(limit),),
                )
            return [dict(row) for row in cur.fetchall()]

    def review_memory_proposal(self, proposal_id: str, action: str, *, reviewer: str,
                               review_metadata: Dict = None) -> Dict[str, Any]:
        """原子批准/拒绝提案；quarantined 永远不能提升为长期记忆。"""
        action = str(action or "").strip().lower()
        if action not in {"approve", "reject"}:
            return {"ok": False, "error": "action 必须是 approve 或 reject"}
        now = datetime.now().isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM memory_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if row is None:
                return {"ok": False, "error": f"未找到记忆提案 {proposal_id}"}
            proposal = dict(row)
            if proposal["status"] not in {"pending", "quarantined"}:
                return {"ok": False, "error": f"提案已是 {proposal['status']} 状态"}

            if action == "reject":
                conn.execute(
                    """UPDATE memory_proposals
                       SET status = 'rejected', reviewed_at = ?, reviewer = ?
                       WHERE id = ?""",
                    (now, reviewer, proposal_id),
                )
                return {"ok": True, "status": "rejected", "proposal_id": proposal_id}

            if proposal["status"] == "quarantined" or proposal["decision"] == "quarantine":
                return {
                    "ok": False,
                    "error": "隔离记录含疑似凭据，不能批准；请提交脱敏后的安全记忆。",
                }

            try:
                metadata = json.loads(proposal.get("metadata") or "{}")
            except (TypeError, ValueError):
                metadata = {}
            metadata["review"] = {
                "proposal_id": proposal_id,
                "reviewer": reviewer,
                "action": "approve",
                "reviewed_at": now,
                **(review_metadata or {}),
            }
            memory_id = str(uuid.uuid4())[:8]
            conn.execute(
                """INSERT INTO memories
                   (id, session_id, type, content, importance, created_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (memory_id, "__longterm__", proposal["memory_type"], proposal["content"],
                 proposal["importance"], now, json.dumps(metadata, ensure_ascii=False)),
            )
            conn.execute(
                """UPDATE memory_proposals
                   SET status = 'approved', reviewed_at = ?, reviewer = ?, memory_id = ?
                   WHERE id = ?""",
                (now, reviewer, memory_id, proposal_id),
            )
            return {
                "ok": True,
                "status": "approved",
                "proposal_id": proposal_id,
                "memory_id": memory_id,
            }

    def delete_memory(self, session_id: str, memory_id: str) -> bool:
        """删除一条记忆。返回是否确实删除。"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                "DELETE FROM memories WHERE session_id = ? AND id = ?",
                (session_id, memory_id)
            )
            return cursor.rowcount > 0
    
    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """获取会话摘要"""
        with sqlite3.connect(str(self.db_path)) as conn:
            # 消息数
            cursor = conn.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,))
            message_count = cursor.fetchone()[0]
            
            # 任务数
            cursor = conn.execute("SELECT COUNT(*) FROM tasks WHERE session_id = ?", (session_id,))
            task_count = cursor.fetchone()[0]
            
            # 编辑数
            cursor = conn.execute("SELECT COUNT(*) FROM edits WHERE session_id = ?", (session_id,))
            edit_count = cursor.fetchone()[0]
            
            # 记忆数
            cursor = conn.execute("SELECT COUNT(*) FROM memories WHERE session_id = ?", (session_id,))
            memory_count = cursor.fetchone()[0]
            
            return {
                "messages": message_count,
                "tasks": task_count,
                "edits": edit_count,
                "memories": memory_count
            }


class SessionManager:
    """会话管理器"""
    
    def __init__(self, db_path: str = ".vortocode/sessions.db"):
        self.store = SessionStore(db_path)
        self.current_session_id: Optional[str] = None
    
    def start_session(self, name: str = None) -> str:
        """开始新会话"""
        self.current_session_id = self.store.create_session(name)
        return self.current_session_id
    
    def resume_session(self, session_id: str) -> bool:
        """恢复会话"""
        session = self.store.get_session(session_id)
        if session:
            self.current_session_id = session_id
            return True
        return False
    
    def get_current_session(self) -> Optional[Dict[str, Any]]:
        """获取当前会话"""
        if self.current_session_id:
            return self.store.get_session(self.current_session_id)
        return None
    
    def add_message(self, role: str, content: str, metadata: Dict = None) -> Optional[str]:
        """添加消息到当前会话"""
        if not self.current_session_id:
            self.start_session()
        
        return self.store.add_message(self.current_session_id, role, content, metadata)
    
    def get_messages(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取当前会话的消息"""
        if not self.current_session_id:
            return []
        return self.store.get_messages(self.current_session_id, limit)
    
    def add_task(self, title: str, description: str = "", 
                 agent_id: str = None, metadata: Dict = None) -> Optional[str]:
        """添加任务"""
        if not self.current_session_id:
            self.start_session()
        
        return self.store.add_task(self.current_session_id, title, description, agent_id, metadata)
    
    def update_task(self, task_id: str, **kwargs) -> bool:
        """更新任务"""
        return self.store.update_task(task_id, **kwargs)
    
    def get_tasks(self, status: str = None) -> List[Dict[str, Any]]:
        """获取任务"""
        if not self.current_session_id:
            return []
        return self.store.get_tasks(self.current_session_id, status)
    
    def add_memory(self, memory_type: str, content: str, 
                   importance: float = 0.5, metadata: Dict = None) -> Optional[str]:
        """添加记忆"""
        if not self.current_session_id:
            self.start_session()
        
        return self.store.add_memory(self.current_session_id, memory_type, content, importance, metadata)
    
    def search_memories(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索记忆"""
        if not self.current_session_id:
            return []
        return self.store.search_memories(self.current_session_id, query, limit)

    def delete_memory(self, memory_id: str) -> bool:
        """删除记忆"""
        if not self.current_session_id:
            return False
        return self.store.delete_memory(self.current_session_id, memory_id)
    
    def list_recent_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """列出最近的会话"""
        return self.store.list_sessions(limit)
    
    def get_session_summary(self) -> Dict[str, Any]:
        """获取当前会话摘要"""
        if not self.current_session_id:
            return {}
        return self.store.get_session_summary(self.current_session_id)
