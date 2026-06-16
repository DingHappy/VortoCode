"""会话持久化 - SQLite 存储"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionStore:
    """会话存储"""
    
    def __init__(self, db_path: str = ".auto-dev-crew/sessions.db"):
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
            
            # 创建索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edits_session ON edits(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id)")
    
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
        """搜索记忆"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            
            cursor = conn.execute(
                "SELECT * FROM memories WHERE session_id = ? AND content LIKE ? ORDER BY importance DESC LIMIT ?",
                (session_id, f"%{query}%", limit)
            )
            
            return [dict(row) for row in cursor.fetchall()]
    
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
    
    def __init__(self, db_path: str = ".auto-dev-crew/sessions.db"):
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
    
    def list_recent_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """列出最近的会话"""
        return self.store.list_sessions(limit)
    
    def get_session_summary(self) -> Dict[str, Any]:
        """获取当前会话摘要"""
        if not self.current_session_id:
            return {}
        return self.store.get_session_summary(self.current_session_id)
