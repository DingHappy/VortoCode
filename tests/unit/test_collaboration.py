"""协作模块特征测试（离线、纯内存逻辑）。

覆盖此前零测试的 src/collaboration/realtime：房间/用户/事件/观察者/冲突解决。
注意：该模块目前未接入任何路由（无消费者）——测试只保证逻辑正确，特性尚不可经 API 触达。
"""

from src.collaboration import (
    CollaborationManager,
    User,
    ConflictResolver,
    CollaborationEventType,
)


def test_join_room_tracks_active_users():
    mgr = CollaborationManager()
    room = mgr.create_room("r1")
    assert mgr.join_room("r1", User(id="u1", name="A")) is True
    assert mgr.join_room("r1", User(id="u2", name="B")) is True
    assert {u.name for u in room.get_active_users()} == {"A", "B"}
    assert any(e.type == CollaborationEventType.USER_JOIN for e in room.events)


def test_join_unknown_room_returns_false():
    assert CollaborationManager().join_room("nope", User(name="X")) is False


def test_leave_room_removes_user():
    mgr = CollaborationManager()
    mgr.create_room("r1")
    mgr.join_room("r1", User(id="u1", name="A"))
    assert mgr.leave_room("u1") is True
    assert mgr.get_room("r1").users == {}


def test_observer_notified_on_events():
    mgr = CollaborationManager()
    room = mgr.create_room("r1")
    seen = []
    room.observe(lambda e: seen.append(e.type))
    room.add_user(User(id="u1", name="A"))
    room.update_cursor("u1", {"line": 3})
    assert CollaborationEventType.USER_JOIN in seen
    assert CollaborationEventType.CURSOR_MOVE in seen


def test_conflict_resolver_last_write_wins():
    r = ConflictResolver()
    changes = [{"timestamp": "2026-01-01", "v": 1}, {"timestamp": "2026-06-01", "v": 2}]
    assert r.resolve(changes)["v"] == 2
    assert r.resolve([]) == {}
