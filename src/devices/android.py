"""Attach-only UiAutomator2 transport for an explicitly bound local session."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

import aiohttp

from src.utils.async_ops import run_with_timeout


class AndroidError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AndroidError("Android 状态含重复字段")
        result[key] = value
    return result


def private_path(path: Path, *, directory=False, create=False):
    if path.is_symlink():
        raise AndroidError("Android 配置/状态不允许符号链接")
    if directory and create and not path.exists():
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise AndroidError("Android 配置/状态身份无效")
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise AndroidError("Android 配置/状态须仅当前用户读写")


@dataclass(frozen=True)
class AndroidBinding:
    endpoint: str
    session_id: str
    udid: str
    package: str
    generation: str
    label: str

    def __post_init__(self):
        try:
            url = urlsplit(self.endpoint)
            valid = (url.scheme == "http" and url.hostname in {"127.0.0.1", "::1"}
                     and url.username is None and url.password is None and url.port is not None
                     and 1 <= url.port <= 65535 and url.path in {"", "/wd/hub"}
                     and not url.query and not url.fragment)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise AndroidError("Appium 仅允许带端口的本机回环地址，不能使用代理、远程主机或登录信息")
        for value in (self.session_id, self.udid, self.generation):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
                raise AndroidError("Android 会话/设备/代次身份无效")
        if not isinstance(self.package, str) or len(self.package) > 255 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", self.package):
            raise AndroidError("Android 应用包身份无效")
        if not isinstance(self.label, str) or not 1 <= len(self.label) <= 60 or any(ord(c) < 32 for c in self.label):
            raise AndroidError("Android 设备标签无效，不要填写凭证")

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True).encode()).hexdigest()


def load_binding(workspace: str, path: Path | None = None) -> AndroidBinding:
    path = path or Path(os.getenv("VORTOONE_ANDROID_SESSION_FILE", ""))
    root = Path(workspace).resolve()
    if not path.is_absolute() or root == path.resolve() or root in path.resolve().parents:
        raise AndroidError("Android 会话配置必须是工作区外的宿主绝对路径")
    private_path(path.parent, directory=True)
    private_path(path)
    # O_NOFOLLOW plus fstat keeps the checked file distinct from a late symlink.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or (os.name != "nt" and
                (info.st_uid != os.getuid() or info.st_mode & 0o077)):
            raise AndroidError("Android 会话配置不可用")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = json.loads(stream.read(4097), object_pairs_hook=unique_object)
    finally:
        os.close(fd)
    if (not isinstance(raw, dict) or set(raw) != {"version", "workspace", "endpoint", "session_id", "udid", "package", "generation", "label"}
            or type(raw["version"]) is not int or raw["version"] != 1 or raw["workspace"] != str(root)):
        raise AndroidError("Android 配置版本或工作区归属不匹配")
    return AndroidBinding(**{key: value for key, value in raw.items() if key not in {"version", "workspace"}})


class AppiumAndroid:
    """Only GET identity/source and one fixed POST actions operation.

    Does not create/delete sessions, run shell, install apps, reset devices,
    grant permissions, change contexts or expose arbitrary Appium commands.
    """
    def __init__(self, binding: AndroidBinding, *, requester=None):
        self.binding = binding
        self._requester = requester

    async def _call(self, method, suffix="", payload=None):
        if (method, suffix) not in {("GET", ""), ("GET", "/context"), ("GET", "/appium/device/current_package"),
                                  ("GET", "/source"), ("POST", "/actions")}:
            raise AndroidError("Android 命令不受支持")
        url = f"{self.binding.endpoint}/session/{self.binding.session_id}{suffix}"
        if self._requester:
            return await self._requester(method, url, payload)
        try:
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=10)) as client:
                async with client.request(method, url, json=payload, allow_redirects=False) as response:
                    if response.status != 200:
                        raise AndroidError("Android 服务未确认请求；不会重试")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        raw.extend(chunk)
                        if len(raw) > 512 * 1024:
                            raise AndroidError("Android 服务响应超过上限")
                    envelope = json.loads(raw, object_pairs_hook=unique_object)
                    if not isinstance(envelope, dict) or "value" not in envelope:
                        raise AndroidError("Android 服务响应无效")
                    value = envelope["value"]
                    if isinstance(value, dict) and "error" in value:
                        raise AndroidError("Android 请求结果不明；请人工核对")
                    return value
        except AndroidError:
            raise
        except Exception:
            raise AndroidError("Android 会话不可用或结果不明；未自动重试") from None

    async def verify(self):
        caps = await self._call("GET")
        if not isinstance(caps, dict):
            raise AndroidError("Android 无法核对会话能力")
        def cap(name):
            plain, prefixed = caps.get(name), caps.get("appium:" + name)
            if plain is not None and prefixed is not None and plain != prefixed:
                raise AndroidError("Android 会话能力冲突")
            return plain if plain is not None else prefixed
        if (str(cap("platformName")).lower() != "android" or str(cap("automationName")).lower() != "uiautomator2"
                or cap("udid") != self.binding.udid or cap("appPackage") != self.binding.package
                or (caps.get("deviceUDID") is not None and caps["deviceUDID"] != self.binding.udid)):
            raise AndroidError("Android 设备、驱动或应用身份已变化")
        if await self._call("GET", "/context") != "NATIVE_APP":
            raise AndroidError("当前不是原生 App，未读取 WebView")
        if await self._call("GET", "/appium/device/current_package") != self.binding.package:
            raise AndroidError("前台不是绑定应用；请真人处理登录或系统弹窗")

    async def observe(self):
        async def operation():
            await self.verify()
            source = await self._call("GET", "/source")
            await self.verify()
            return source
        return await run_with_timeout(operation(), 20)

    async def tap(self, x, y):
        result = await self._call("POST", "/actions", {"actions": [{"type": "pointer", "id": "vortocode-touch",
            "parameters": {"pointerType": "touch"}, "actions": [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y, "origin": "viewport"},
                {"type": "pointerDown", "button": 0}, {"type": "pause", "duration": 80},
                {"type": "pointerUp", "button": 0}]}]})
        if result is not None:
            raise AndroidError("Android 点按返回了未知结果，须人工核对")
