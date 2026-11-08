#!/usr/bin/env python3
"""常驻助理数据的加密备份（2026-11-02）：目标、长期记忆、主要聊天、手机钥匙登记、撤销记录等。

    keygen  --private ~/.config/vortocode/backup-key.pem          在主人的电脑上生成密钥对（私钥只留在这里）
    backup  --public-key KEY.pub --staging DIR [--target user@host:path] [--keep 30]
    restore --private KEY.pem --archive FILE --into DIR            解开一份备份

- **只有公钥放在常驻电脑上**：每份备份用一次性的 X25519 密钥 + HKDF-SHA256 + AES-256-GCM 加密给主人的公钥。
  NAS 被拿走、常驻电脑被攻破都读不出旧备份；只有主人手里的私钥能解。
- SQLite 用在线备份接口拷一致的快照，不直接复制正在写的数据库文件。
- 本地暂存目录只留最近 ``--keep`` 份；``--target`` 用 rsync 镜像过去（远端同样只留这些）。
- 成功 / 失败都报到常驻助理的运维心跳（``source=assistant-backup``，30 小时没报就推送主人）：
  需要 ``VORTOONE_OPS_TOKEN``；没配就跳过报到。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

MAGIC = b"VCBAK1\n"
HOME = Path.home()


def _state_dir(base: Path) -> Path:
    """状态目录改名为 .vortoone 后旧名 .vortocode 留的是链接（ADR-018 批次 C）：备份真实目录，不备链接。"""
    for name in (".vortoone", ".vortocode"):
        candidate = base / name
        if candidate.is_dir() and not candidate.is_symlink():
            return candidate
    return base / ".vortoone"


DEFAULT_SOURCES = [
    _state_dir(HOME / "assistant"),
    _state_dir(HOME),
    HOME / ".config" / "vortocode-mobile",
    HOME / ".config" / "vortocode-https",
]
SKIP_NAMES = {"__pycache__", "cache", "tmp", ".lock", "runtime.lock"}
SKIP_SUFFIXES = (".sock", ".pid", "-shm", "-wal")


def _crypto():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return hashes, serialization, X25519PrivateKey, X25519PublicKey, AESGCM, HKDF


def _derive(shared: bytes, salt: bytes) -> bytes:
    hashes, _s, _a, _b, _c, HKDF = _crypto()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"vortocode-assistant-backup").derive(shared)


def keygen(private_path: Path) -> str:
    _h, serialization, X25519PrivateKey, _p, _a, _k = _crypto()
    if private_path.exists():
        raise SystemExit(f"{private_path} 已存在，不覆盖")
    key = X25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return public


def encrypt(plain: bytes, public_hex: str) -> bytes:
    _h, serialization, X25519PrivateKey, X25519PublicKey, AESGCM, _k = _crypto()
    recipient = X25519PublicKey.from_public_bytes(bytes.fromhex(public_hex.strip()))
    ephemeral = X25519PrivateKey.generate()
    eph_pub = ephemeral.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = _derive(ephemeral.exchange(recipient), eph_pub)
    nonce = os.urandom(12)
    return MAGIC + eph_pub + nonce + AESGCM(key).encrypt(nonce, plain, MAGIC)


def decrypt(blob: bytes, private_pem: bytes) -> bytes:
    _h, serialization, _x, _p, AESGCM, _k = _crypto()
    if not blob.startswith(MAGIC):
        raise SystemExit("不是助理备份文件")
    body = blob[len(MAGIC):]
    eph_pub, nonce, sealed = body[:32], body[32:44], body[44:]
    private = serialization.load_pem_private_key(private_pem, password=None)
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    key = _derive(private.exchange(X25519PublicKey.from_public_bytes(eph_pub)), eph_pub)
    return AESGCM(key).decrypt(nonce, sealed, MAGIC)


def _skip(path: Path, source: Path) -> bool:
    """跳过缓存、锁、套接字、SQLite 的旁路文件（快照里已含）和符号链接（不跟出备份范围）。"""
    rel = path.relative_to(source)
    return (any(part in SKIP_NAMES for part in rel.parts) or path.name.endswith(SKIP_SUFFIXES)
            or path.is_symlink() or any(parent.is_symlink() for parent in path.parents if source in parent.parents))


def archive(sources: list[Path]) -> bytes:
    """打成 tar.gz；SQLite 文件用在线备份接口拷一致快照。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar, tempfile.TemporaryDirectory() as scratch:
        for source in sources:
            if not source.is_dir():
                continue
            base = source.relative_to(HOME) if source.is_relative_to(HOME) else Path(source.name)
            for path in sorted(source.rglob("*")):
                if _skip(path, source) or not path.is_file():
                    continue
                name = str(base / path.relative_to(source))
                if path.suffix in (".db", ".sqlite", ".sqlite3"):
                    copy = Path(scratch) / f"snap-{abs(hash(name))}.db"
                    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as live, sqlite3.connect(copy) as snap:
                        live.backup(snap)
                    tar.add(copy, arcname=name)
                else:
                    tar.add(path, arcname=name)
    return buffer.getvalue()


def _env(name: str, default: str = "") -> str:
    """先读 VORTOONE_*，没有再读改名前的 VORTOCODE_*（ADR-018 批次 B；本脚本不导入 src）。"""
    value = os.getenv("VORTOONE_" + name)
    return value if value is not None else os.getenv("VORTOCODE_" + name, default)


def heartbeat(ok: bool, text: str) -> None:
    token = _env("OPS_TOKEN").strip()
    if not token:
        return
    url = _env("OPS_URL", "http://127.0.0.1:8081").rstrip("/") + "/api/ops/heartbeat"
    request = urllib.request.Request(url, method="POST", headers={"Authorization": "Bearer " + token,
                                                                   "Content-Type": "application/json"},
                                     data=json.dumps({"source": "assistant-backup", "ok": ok, "text": text[:200],
                                                      "expect_hours": 30}).encode())
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
    except OSError as error:
        print(f"运维心跳报到失败：{type(error).__name__}", file=sys.stderr)


def backup(args) -> Path:
    staging = Path(args.staging).expanduser()
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    public = Path(args.public_key).expanduser().read_text().strip()
    sources = [Path(s).expanduser() for s in args.source] if args.source else DEFAULT_SOURCES
    blob = encrypt(archive(sources), public)
    name = time.strftime("assistant-%Y%m%d-%H%M%S.vcbak")
    target = staging / name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(blob)
    kept = sorted(staging.glob("assistant-*.vcbak"))
    for old in kept[:-args.keep]:
        old.unlink()
    if args.target:
        subprocess.run(["rsync", "-a", "--delete", "--include", "assistant-*.vcbak", "--exclude", "*",
                        "-e", "ssh -o BatchMode=yes -o ConnectTimeout=20", f"{staging}/", args.target],
                       check=True, timeout=600)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--private", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--public-key", required=True)
    b.add_argument("--staging", required=True)
    b.add_argument("--target", default="")
    b.add_argument("--keep", type=int, default=30)
    b.add_argument("--source", action="append", default=[])
    r = sub.add_parser("restore")
    r.add_argument("--private", required=True)
    r.add_argument("--archive", required=True)
    r.add_argument("--into", required=True)
    args = parser.parse_args(argv)
    if args.command == "keygen":
        public = keygen(Path(args.private).expanduser())
        print(public)
        return 0
    if args.command == "restore":
        plain = decrypt(Path(args.archive).expanduser().read_bytes(), Path(args.private).expanduser().read_bytes())
        into = Path(args.into).expanduser()
        into.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
            try:
                tar.extractall(into, filter="data")      # 拒绝绝对路径、..、设备文件等
            except TypeError:                            # 旧版 Python 没有 filter：逐个核对再解
                root = into.resolve()
                for member in tar.getmembers():
                    target = (into / member.name).resolve()
                    if not (member.isfile() or member.isdir()) or root not in [target, *target.parents]:
                        raise SystemExit(f"备份里有不安全的条目：{member.name}")
                tar.extractall(into)
        print(f"已解开到 {into}")
        return 0
    try:
        target = backup(args)
    except Exception as error:  # noqa: BLE001 —— 任何失败都要报到，否则「没备份」不会有人知道
        heartbeat(False, f"助理数据备份失败：{type(error).__name__}: {str(error)[:120]}")
        raise
    heartbeat(True, f"助理数据已备份：{target.name}（{target.stat().st_size // 1024} KB）")
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
