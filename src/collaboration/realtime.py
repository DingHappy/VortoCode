"""实时协作系统 - 多用户协作"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CollaborationEventType(str, Enum):
    """协作事件类型"""
    USER_JOIN = "user_join"
    USER_LEAVE = "user_leave"
    CURSOR_MOVE = "cursor_move"
    CONTENT_CHANGE = "content_change"
    SELECTION_CHANGE = "selection_change"
    COMMENT_ADD = "comment_add"
    TASK_ASSIGN = "task_assign"
    STATUS_CHANGE = "status_change"


class CollaborationEvent(BaseModel):
    """协作事件"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: CollaborationEventType
    user_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    data: Dict[str, Any] = Field(default_factory=dict)


class User(BaseModel):
    """用户"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    email: str = ""
    avatar: str = ""
    status: str = "online"  # online, away, offline
    cursor_position: Optional[Dict[str, Any]] = None
    current_file: Optional[str] = None


class CollaborationRoom:
    """协作房间"""
    
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.users: Dict[str, User] = {}
        self.events: List[CollaborationEvent] = []
        self.state: Dict[str, Any] = {}
        self.observers: List[Callable] = []
    
    def add_user(self, user: User):
        """添加用户"""
        self.users[user.id] = user
        
        event = CollaborationEvent(
            type=CollaborationEventType.USER_JOIN,
            user_id=user.id,
            data={"name": user.name}
        )
        self._add_event(event)
    
    def remove_user(self, user_id: str):
        """移除用户"""
        if user_id in self.users:
            del self.users[user_id]
            
            event = CollaborationEvent(
                type=CollaborationEventType.USER_LEAVE,
                user_id=user_id
            )
            self._add_event(event)
    
    def update_cursor(self, user_id: str, position: Dict[str, Any]):
        """更新光标位置"""
        if user_id in self.users:
            self.users[user_id].cursor_position = position
            
            event = CollaborationEvent(
                type=CollaborationEventType.CURSOR_MOVE,
                user_id=user_id,
                data={"position": position}
            )
            self._add_event(event)
    
    def change_content(self, user_id: str, file: str, changes: Dict[str, Any]):
        """内容变更"""
        event = CollaborationEvent(
            type=CollaborationEventType.CONTENT_CHANGE,
            user_id=user_id,
            data={"file": file, "changes": changes}
        )
        self._add_event(event)
    
    def add_comment(self, user_id: str, file: str, line: int, comment: str):
        """添加评论"""
        event = CollaborationEvent(
            type=CollaborationEventType.COMMENT_ADD,
            user_id=user_id,
            data={"file": file, "line": line, "comment": comment}
        )
        self._add_event(event)
    
    def _add_event(self, event: CollaborationEvent):
        """添加事件"""
        self.events.append(event)
        
        # 通知观察者
        for observer in self.observers:
            try:
                observer(event)
            except Exception as e:
                logger.error(f"Observer error: {e}")
    
    def observe(self, callback: Callable):
        """添加观察者"""
        self.observers.append(callback)
    
    def get_active_users(self) -> List[User]:
        """获取活跃用户"""
        return [u for u in self.users.values() if u.status == "online"]
    
    def get_recent_events(self, limit: int = 50) -> List[CollaborationEvent]:
        """获取最近事件"""
        return self.events[-limit:]


class CollaborationManager:
    """协作管理器"""
    
    def __init__(self):
        self.rooms: Dict[str, CollaborationRoom] = {}
        self.user_rooms: Dict[str, str] = {}  # user_id -> room_id
    
    def create_room(self, room_id: str = None) -> CollaborationRoom:
        """创建房间"""
        room_id = room_id or str(uuid.uuid4())[:8]
        room = CollaborationRoom(room_id)
        self.rooms[room_id] = room
        return room
    
    def join_room(self, room_id: str, user: User) -> bool:
        """加入房间"""
        room = self.rooms.get(room_id)
        if room:
            room.add_user(user)
            self.user_rooms[user.id] = room_id
            return True
        return False
    
    def leave_room(self, user_id: str) -> bool:
        """离开房间"""
        room_id = self.user_rooms.get(user_id)
        if room_id and room_id in self.rooms:
            self.rooms[room_id].remove_user(user_id)
            del self.user_rooms[user_id]
            return True
        return False
    
    def get_room(self, room_id: str) -> Optional[CollaborationRoom]:
        """获取房间"""
        return self.rooms.get(room_id)
    
    def get_user_room(self, user_id: str) -> Optional[CollaborationRoom]:
        """获取用户所在房间"""
        room_id = self.user_rooms.get(user_id)
        if room_id:
            return self.rooms.get(room_id)
        return None
    
    async def broadcast(self, room_id: str, event: CollaborationEvent):
        """广播事件"""
        room = self.rooms.get(room_id)
        if room:
            room._add_event(event)


class ConflictResolver:
    """冲突解决器"""
    
    def resolve(self, changes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """解决冲突"""
        # 简化的冲突解决：最后修改胜出
        if not changes:
            return {}
        
        # 按时间排序
        sorted_changes = sorted(
            changes,
            key=lambda x: x.get("timestamp", ""),
            reverse=True
        )
        
        return sorted_changes[0]


class OperationalTransform:
    """操作转换"""
    
    def transform(self, op1: Dict[str, Any], op2: Dict[str, Any]) -> Dict[str, Any]:
        """转换操作"""
        # 简化的操作转换
        # 实际实现需要更复杂的逻辑
        return op2
