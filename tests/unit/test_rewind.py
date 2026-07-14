"""checkpoint/rewind（src/memory/rewind.py + session_store 的 edits 表）的单测。

钉住的行为：记录→按回合 LIFO 撤销→成功即消费（再撤就是上一回合）；被手改过的文件跳过
不覆盖；越界路径拒绝还原；新建文件的撤销 = 删除。全离线（tmp SQLite + tmp 工作区）。
"""

import pytest

from src.memory.rewind import group_turns, record_edit, rewind_turns
from src.memory.session_store import SessionStore


@pytest.fixture()
def store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


@pytest.fixture()
def sid(store):
    return store.create_session("rewind-test")


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ------------------------------------------------------------ 基本撤销
def test_rewind_restores_old_content(store, sid, tmp_path):
    p = _w(tmp_path, "a.py", "v2")
    record_edit(store, sid, "a.py", "v1", "v2", turn_id="t1", tool="edit_file")
    res = rewind_turns(store, sid, str(tmp_path), n=1)
    assert res["reverted"] == ["a.py"] and not res["skipped"]
    assert p.read_text(encoding="utf-8") == "v1"
    assert store.get_edits(sid) == []                       # 成功还原的记录被消费


def test_rewind_created_file_is_deleted(store, sid, tmp_path):
    p = _w(tmp_path, "new.py", "hello")
    record_edit(store, sid, "new.py", None, "hello", turn_id="t1", tool="write_file")
    res = rewind_turns(store, sid, str(tmp_path), n=1)
    assert res["reverted"] == ["new.py"]
    assert not p.exists()                                   # 新建文件的撤销 = 删除，而非回写空串


# ------------------------------------------------------------ 保护：不覆盖手改、不越界
def test_rewind_skips_hand_modified_file(store, sid, tmp_path):
    p = _w(tmp_path, "a.py", "v3")                          # 用户在 agent 写完 v2 后手改成了 v3
    record_edit(store, sid, "a.py", "v1", "v2", turn_id="t1", tool="edit_file")
    res = rewind_turns(store, sid, str(tmp_path), n=1)
    assert res["reverted"] == []
    assert len(res["skipped"]) == 1 and "不覆盖手改" in res["skipped"][0][1]
    assert p.read_text(encoding="utf-8") == "v3"            # 文件纹丝不动
    assert len(store.get_edits(sid)) == 1                   # 跳过的记录不消费


def test_rewind_refuses_path_escape(store, sid, tmp_path):
    outside = tmp_path / "evil.txt"
    outside.write_text("secret", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    record_edit(store, sid, "../evil.txt", "old", "secret", turn_id="t1", tool="write_file")
    res = rewind_turns(store, sid, str(repo), n=1)
    assert res["reverted"] == []
    assert "越界" in res["skipped"][0][1]
    assert outside.read_text(encoding="utf-8") == "secret"


# ------------------------------------------------------------ 回合分组与 LIFO
def test_rewind_lifo_turn_by_turn(store, sid, tmp_path):
    p = _w(tmp_path, "a.py", "v3")
    record_edit(store, sid, "a.py", "v1", "v2", turn_id="t1", tool="edit_file")
    record_edit(store, sid, "a.py", "v2", "v3", turn_id="t2", tool="edit_file")
    res1 = rewind_turns(store, sid, str(tmp_path), n=1)     # 只撤最近的 t2
    assert res1["turns"] == ["t2"] and p.read_text(encoding="utf-8") == "v2"
    res2 = rewind_turns(store, sid, str(tmp_path), n=1)     # 记录已消费 → 这次撤的是 t1
    assert res2["turns"] == ["t1"] and p.read_text(encoding="utf-8") == "v1"


def test_rewind_multiple_edits_same_turn_chain(store, sid, tmp_path):
    p = _w(tmp_path, "a.py", "v3")                          # 同一回合内 v1→v2→v3
    record_edit(store, sid, "a.py", "v1", "v2", turn_id="t1", tool="edit_file")
    record_edit(store, sid, "a.py", "v2", "v3", turn_id="t1", tool="edit_file")
    res = rewind_turns(store, sid, str(tmp_path), n=1)
    assert res["reverted"] == ["a.py", "a.py"]              # 逆序成链：先撤 v3→v2 再撤 v2→v1
    assert p.read_text(encoding="utf-8") == "v1"


def test_rewind_n_covers_multiple_turns(store, sid, tmp_path):
    pa = _w(tmp_path, "a.py", "a2")
    pb = _w(tmp_path, "b.py", "b2")
    record_edit(store, sid, "a.py", "a1", "a2", turn_id="t1", tool="edit_file")
    record_edit(store, sid, "b.py", "b1", "b2", turn_id="t2", tool="edit_file")
    res = rewind_turns(store, sid, str(tmp_path), n=2)
    assert sorted(res["reverted"]) == ["a.py", "b.py"]
    assert pa.read_text(encoding="utf-8") == "a1" and pb.read_text(encoding="utf-8") == "b1"


def test_group_turns_new_to_old(store, sid, tmp_path):
    record_edit(store, sid, "a.py", "a1", "a2", turn_id="t1", tool="edit_file")
    record_edit(store, sid, "b.py", "b1", "b2", turn_id="t2", tool="edit_file")
    groups = group_turns(store, sid)
    assert [g["turn"] for g in groups] == ["t2", "t1"]      # 新 → 旧
    assert groups[0]["files"] == ["b.py"]


def test_rewind_empty_session_is_noop(store, sid, tmp_path):
    res = rewind_turns(store, sid, str(tmp_path), n=1)
    assert res == {"reverted": [], "skipped": [], "turns": []}


def test_record_edit_never_raises_without_store():
    assert record_edit(None, "", "a.py", "x", "y", turn_id="t", tool="edit_file") is None
