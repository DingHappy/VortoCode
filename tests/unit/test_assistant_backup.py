"""scripts/assistant-backup.py：常驻助理数据的加密备份。只有公钥在常驻电脑上，私钥在主人手里；
SQLite 拷一致快照；本地只留最近 N 份；失败要报到运维心跳。"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "assistant_backup", Path(__file__).resolve().parents[2] / "scripts" / "assistant-backup.py")
backup_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_mod)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_mod, "HOME", tmp_path)
    data = tmp_path / "assistant" / ".vortoone"
    (data / "goals").mkdir(parents=True)
    (data / "goals" / "personal-1.json").write_text('{"objective": "交周报"}')
    (data / "cache").mkdir()
    (data / "cache" / "junk.bin").write_text("x")
    (data / "runtime.lock").write_text("")
    with sqlite3.connect(data / "sessions.db") as db:
        db.execute("create table memories (content text)")
        db.execute("insert into memories values ('喜欢简短的回复')")
    (data / "sessions.db-wal").write_text("stale")
    private = tmp_path / "keys" / "backup-key.pem"
    public = backup_mod.keygen(private)
    (tmp_path / "backup.pub").write_text(public)
    return tmp_path, data, private


def test_round_trip_restores_data_and_a_consistent_database(home):
    root, data, private = home
    out = backup_mod.backup(type("A", (), {"staging": str(root / "staging"), "public_key": str(root / "backup.pub"),
                                           "source": [str(data)], "keep": 30, "target": ""})())
    blob = out.read_bytes()
    assert blob.startswith(backup_mod.MAGIC) and b"\xe4\xba\xa4\xe5\x91\xa8\xe6\x8a\xa5" not in blob   # 「交周报」不以明文出现
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    assert backup_mod.main(["restore", "--private", str(private), "--archive", str(out), "--into", str(root / "back")]) == 0
    restored = root / "back" / "assistant" / ".vortoone"
    assert (restored / "goals" / "personal-1.json").read_text() == '{"objective": "交周报"}'
    with sqlite3.connect(restored / "sessions.db") as db:
        assert db.execute("select content from memories").fetchall() == [("喜欢简短的回复",)]
    assert not (restored / "cache").exists() and not (restored / "runtime.lock").exists()
    assert not (restored / "sessions.db-wal").exists()


def test_only_the_private_key_opens_it(home, tmp_path):
    root, data, _private = home
    out = backup_mod.backup(type("A", (), {"staging": str(root / "staging"), "public_key": str(root / "backup.pub"),
                                           "source": [str(data)], "keep": 30, "target": ""})())
    other = tmp_path / "other.pem"
    backup_mod.keygen(other)
    with pytest.raises(Exception):
        backup_mod.decrypt(out.read_bytes(), other.read_bytes())


def test_keeps_only_the_latest_copies(home, monkeypatch):
    root, data, _private = home
    names = iter(f"assistant-2026110{i}-000000.vcbak" for i in range(1, 6))
    monkeypatch.setattr(backup_mod.time, "strftime", lambda _fmt: next(names))
    args = type("A", (), {"staging": str(root / "staging"), "public_key": str(root / "backup.pub"),
                          "source": [str(data)], "keep": 3, "target": ""})()
    for _ in range(5):
        backup_mod.backup(args)
    assert sorted(p.name for p in (root / "staging").glob("*.vcbak")) == [
        "assistant-20261103-000000.vcbak", "assistant-20261104-000000.vcbak", "assistant-20261105-000000.vcbak"]


def test_failure_is_reported_to_the_ops_heartbeat(home, monkeypatch):
    root, data, _private = home
    reports = []
    monkeypatch.setattr(backup_mod, "heartbeat", lambda ok, text: reports.append((ok, text)))
    with pytest.raises(Exception):
        backup_mod.main(["backup", "--public-key", str(root / "missing.pub"), "--staging", str(root / "s"),
                         "--source", str(data)])
    assert reports and reports[0][0] is False and "备份失败" in reports[0][1]
    reports.clear()
    assert backup_mod.main(["backup", "--public-key", str(root / "backup.pub"), "--staging", str(root / "s"),
                            "--source", str(data)]) == 0
    assert reports[0][0] is True


def test_keygen_never_overwrites(home):
    _root, _data, private = home
    with pytest.raises(SystemExit):
        backup_mod.keygen(private)


def test_backs_up_the_real_state_directory_before_and_after_the_rename(tmp_path):
    """状态目录改名后 .vortocode 是指向 .vortoone 的链接（ADR-018 批次 C）：备份要的是真实目录；没搬的旧目录照样备份。"""
    old = tmp_path / "before" / ".vortocode"
    old.mkdir(parents=True)
    assert backup_mod._state_dir(tmp_path / "before") == old
    moved = tmp_path / "after"
    (moved / ".vortoone").mkdir(parents=True)
    (moved / ".vortocode").symlink_to(".vortoone")
    assert backup_mod._state_dir(moved) == moved / ".vortoone"
