"""Host-local website grants. Cookies stay in Chromium's separate profiles.

The tool adapter calls approve/revoke only after the original human INTERACT
gate. No password, Cookie, browser storage state or task execution lives here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit
import uuid

from src.gateway.process_lease import ProcessLease
from src.browser.backend import DEFAULT_BACKEND_ID, validate_backend_id
from src.utils.state_dir import home_state_dir

MAX_CONNECTIONS = 64
MAX_BYTES = 128 * 1024


def site_origin(url: str) -> str:
    try:
        value = urlsplit(url)
        if value.scheme != "https" or not value.hostname or value.username is not None or value.password is not None:
            raise ValueError()
        host = value.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        port = value.port
        host = f"[{host}]" if ":" in host else host
        return f"https://{host}" + (f":{port}" if port not in (None, 443) else "")
    except (ValueError, UnicodeError):
        raise ValueError("网站连接需要不含账号密码的 HTTPS 地址") from None


def safe_url(url: str) -> str:
    """Display no query/fragment/userinfo; login callbacks can carry credentials."""
    try:
        value = urlsplit(url)
        if value.scheme not in {"http", "https"}:
            return "about:blank" if url == "about:blank" else "（地址已隐藏）"
        host = value.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = value.port
        return f"{value.scheme}://{host}" + (f":{port}" if port else "") + "/"
    except ValueError:
        return "（地址已隐藏）"


class BrowserConnections:
    def __init__(self, workspace: str, *, state_root: Path | None = None):
        self.workspace = str(Path(workspace).resolve())
        self.scope = hashlib.sha256(self.workspace.encode()).hexdigest()
        self.root = state_root or home_state_dir() / "website-connections"
        canonical = self.root.resolve()
        if canonical == Path(self.workspace) or Path(self.workspace) in canonical.parents:
            raise ValueError("网站授权和登录会话必须保存在工作区之外")
        self.path = self.root / "connections.json"

    def _private(self, path: Path, *, directory=False):
        parent = self.root.parent
        if parent.is_symlink() or (parent.exists() and hasattr(os, "getuid") and
                (parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o022)):
            raise ValueError("网站连接宿主目录必须可信且不可由其他用户修改")
        if path.is_symlink():
            raise ValueError("网站连接路径不能使用符号链接")
        if not path.exists():
            if not directory:
                return
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = path.stat()
        if (directory and not stat.S_ISDIR(info.st_mode)) or (not directory and not stat.S_ISREG(info.st_mode)):
            raise ValueError("网站连接状态身份无效")
        if hasattr(os, "getuid") and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError("网站连接状态必须仅由当前用户读写")

    def _read(self):
        self._private(self.root, directory=True)
        self._private(self.path)
        if not self.path.exists():
            return []
        if self.path.stat().st_size > MAX_BYTES:
            raise ValueError("网站连接状态超过容量")
        def unique(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError("网站连接状态存在重复字段")
                out[key] = value
            return out
        try:
            raw = json.loads(self.path.read_text(), object_pairs_hook=unique)
            if not isinstance(raw, dict) or set(raw) != {"version", "connections"} or type(raw["version"]) is not int or raw["version"] != 1:
                raise ValueError()
            rows = raw["connections"]
            if not isinstance(rows, list) or len(rows) > MAX_CONNECTIONS:
                raise ValueError()
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"id", "scope", "origin", "account_label", "device_mode"}:
                    raise ValueError()
                if not re.fullmatch(r"[a-f0-9]{32}", row["id"]) or row["id"] in seen \
                        or not re.fullmatch(r"[a-f0-9]{64}", row["scope"]) or row["device_mode"] not in {"desktop", "mobile"}:
                    raise ValueError()
                if site_origin(row["origin"]) != row["origin"] or self.label(row["account_label"]) != row["account_label"]:
                    raise ValueError()
                seen.add(row["id"])
            return rows
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError("网站连接状态损坏或不兼容；不会猜测账号或加载旧登录目录") from None

    @staticmethod
    def label(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 60 or any(ord(c) < 32 for c in value):
            raise ValueError("请使用简短账号标签，不要填写密码、Cookie 或验证码")
        return value.strip()

    def list(self):
        return [{key: row[key] for key in ("id", "origin", "account_label", "device_mode")}
                for row in self._read() if row["scope"] == self.scope]

    def get(self, connection_id):
        return next((row for row in self.list() if row["id"] == connection_id), None)

    def _write(self, rows):
        raw = json.dumps({"version": 1, "connections": rows}, ensure_ascii=False).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError("网站连接状态超过容量")
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".pending-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            self._private(self.path)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def approve(self, url, account_label, device_mode):
        origin, label = site_origin(url), self.label(account_label)
        if device_mode not in {"desktop", "mobile"}:
            raise ValueError("网页模式只能为 desktop 或 mobile；不是原生手机 App")
        self._private(self.root, directory=True)
        with ProcessLease(self.root / "registry.lock"):
            rows = self._read()
            if len(rows) >= MAX_CONNECTIONS:
                raise ValueError("网站连接已满，请先明确撤销不再使用的连接")
            row = {"id": uuid.uuid4().hex, "scope": self.scope, "origin": origin,
                   "account_label": label, "device_mode": device_mode}
            self._write([*rows, row])
        return {key: value for key, value in row.items() if key != "scope"}

    def revoke(self, connection_id):
        self._private(self.root, directory=True)
        with ProcessLease(self.root / "registry.lock"):
            rows = self._read()
            own = any(row["id"] == connection_id and row["scope"] == self.scope for row in rows)
            if own:
                self._write([row for row in rows if not (row["id"] == connection_id and row["scope"] == self.scope)])
            return own

    def profile_path(self, connection_id, *, backend_id=DEFAULT_BACKEND_ID):
        validate_backend_id(backend_id)
        if self.get(connection_id) is None:
            raise ValueError("网站连接不存在、已撤销或不属于此工作区")
        # Keep the original CDP location. Other reviewed backends never inherit
        # its cookies, even when the same website connection is selected.
        base = self.root / ("profiles" if backend_id == DEFAULT_BACKEND_ID else "driver-profiles")
        self._private(base, directory=True)
        target = base / connection_id
        self._private(target, directory=True)
        if backend_id != DEFAULT_BACKEND_ID:
            target = target / backend_id
            self._private(target, directory=True)
        return target
