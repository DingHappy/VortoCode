"""独立浏览器：用本机 Chrome 起一个**独立配置目录**的实例，经 DevTools 管道驱动。

为什么这样做（而不是 Playwright / 接管用户的 Chrome）：
- 独立 ``--user-data-dir``：看不到用户日常浏览器里的登录态、Cookie 和密码；需要登录的站点
  由用户在这个窗口里自己登录。
- ``--remote-debugging-pipe``：DevTools 走父子进程间的管道（fd 3/4），**不开 TCP 端口**，
  本机其他进程无法连上来驱动它。
- 不打包 Playwright 和 Chromium：复用本机已装的 Chrome / Edge / Chromium（Desktop sidecar
  刻意排除了 playwright，见 desktop/scripts/build-sidecar.mjs）。

本模块只管"起进程 + 收发 CDP"，安全策略（哪些站点能碰、哪些动作要确认）在
``src/agents/tools/browser.py``。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
_LINUX_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge")

PROFILE_DIR = Path.home() / ".vortocode" / "browser-profile"
_COMMAND_TIMEOUT = 30.0


class BrowserError(RuntimeError):
    pass


def find_browser() -> Optional[str]:
    """本机可用的 Chromium 系浏览器路径；``VORTOCODE_BROWSER_PATH`` 优先。"""
    override = (os.getenv("VORTOCODE_BROWSER_PATH") or "").strip()
    if override:
        return override if os.access(override, os.X_OK) else None
    for path in _CANDIDATES:
        if os.access(path, os.X_OK):
            return path
    for name in _LINUX_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


class CdpBrowser:
    """一个独立浏览器实例 + 一个受控标签页（flatten 会话）。"""

    def __init__(self, executable: str, profile_dir: Path = PROFILE_DIR):
        self.executable = executable
        self.profile_dir = profile_dir
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._writer_fd: Optional[int] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._session: Optional[str] = None
        self._pump: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        if self.running:
            return
        self.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        to_child_r, to_child_w = os.pipe()     # 我们写 → 浏览器从 fd 3 读
        from_child_r, from_child_w = os.pipe()  # 浏览器写 fd 4 → 我们读

        def _wire_fds() -> None:  # 子进程里把两端放到 Chrome 约定的 3/4 号描述符
            import fcntl
            # 先挪到高位再 dup2：原描述符本身可能就是 3 或 4，直接 dup2 会互相覆盖。
            read_end = fcntl.fcntl(to_child_r, fcntl.F_DUPFD, 10)
            write_end = fcntl.fcntl(from_child_w, fcntl.F_DUPFD, 10)
            os.dup2(read_end, 3)
            os.dup2(write_end, 4)

        args = [
            self.executable,
            f"--user-data-dir={self.profile_dir}",
            "--remote-debugging-pipe",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,OptimizationHints",
            "--window-size=1280,900",
            "about:blank",
        ]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                # 不能用 pass_fds：子进程会在 preexec_fn **之后**关掉名单外的描述符，刚 dup 到
                # 3/4 的两端也一起被关（Chrome 报 "pipe file descriptors are not open"）。
                # Python 打开的描述符默认不可继承，close_fds=False 不会把别的句柄漏给浏览器。
                close_fds=False, preexec_fn=_wire_fds)
        finally:
            os.close(to_child_r)
            os.close(from_child_w)
        self._writer_fd = to_child_w
        loop = asyncio.get_running_loop()
        self._reader = asyncio.StreamReader(limit=64 * 1024 * 1024)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(self._reader),
                                     os.fdopen(from_child_r, "rb", buffering=0))
        self._pump = asyncio.create_task(self._read_loop())
        await self._attach_page()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                raw = await self._reader.readuntil(b"\0")
                try:
                    message = json.loads(raw[:-1])
                except ValueError:
                    continue
                future = self._pending.pop(message.get("id"), None) if "id" in message else None
                if future is not None and not future.done():
                    if "error" in message:
                        future.set_exception(BrowserError(str(message["error"].get("message") or message["error"])))
                    else:
                        future.set_result(message.get("result") or {})
        except (asyncio.IncompleteReadError, asyncio.CancelledError, ConnectionError):
            pass
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(BrowserError("浏览器已关闭"))
            self._pending.clear()

    async def send(self, method: str, params: Optional[dict] = None, *, page: bool = True,
                   timeout: float = _COMMAND_TIMEOUT) -> Dict[str, Any]:
        if not self.running or self._writer_fd is None:
            raise BrowserError("浏览器未启动或已关闭")
        self._next_id += 1
        message: Dict[str, Any] = {"id": self._next_id, "method": method, "params": params or {}}
        if page and self._session:
            message["sessionId"] = self._session
        future = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = future
        payload = json.dumps(message).encode() + b"\0"
        await asyncio.to_thread(os.write, self._writer_fd, payload)
        return await asyncio.wait_for(future, timeout)

    async def _attach_page(self) -> None:
        targets = await self.send("Target.getTargets", page=False)
        pages = [t for t in targets.get("targetInfos", []) if t.get("type") == "page"]
        target_id = pages[0]["targetId"] if pages else (
            await self.send("Target.createTarget", {"url": "about:blank"}, page=False))["targetId"]
        attached = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True}, page=False)
        self._session = attached["sessionId"]
        await self.send("Page.enable")
        await self.send("Runtime.enable")

    async def evaluate(self, expression: str) -> Any:
        result = await self.send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise BrowserError(str(result["exceptionDetails"].get("text") or "页面脚本出错"))
        return (result.get("result") or {}).get("value")

    async def navigate(self, url: str) -> None:
        result = await self.send("Page.navigate", {"url": url})
        if result.get("errorText"):
            raise BrowserError(f"打开失败：{result['errorText']}")
        await self.wait_ready()

    async def wait_ready(self, timeout: float = 15.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                if await self.evaluate("document.readyState") in ("interactive", "complete"):
                    await asyncio.sleep(0.4)  # 给首屏脚本一点时间渲染出可交互元素
                    return
            except BrowserError:
                pass
            await asyncio.sleep(0.3)

    async def click_at(self, x: float, y: float) -> None:
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent", {
                "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})

    async def insert_text(self, text: str) -> None:
        await self.send("Input.insertText", {"text": text})

    async def press_enter(self) -> None:
        for kind in ("keyDown", "keyUp"):
            await self.send("Input.dispatchKeyEvent", {
                "type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"})

    async def screenshot(self) -> bytes:
        import base64
        result = await self.send("Page.captureScreenshot", {"format": "png"})
        return base64.b64decode(result.get("data") or "")

    async def close(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            try:
                await self.send("Browser.close", page=False, timeout=5)
            except Exception:  # noqa: BLE001 —— 关不掉就直接结束进程
                self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except asyncio.TimeoutError:
                self._proc.kill()
        if self._pump is not None:
            self._pump.cancel()
        if self._writer_fd is not None:
            try:
                os.close(self._writer_fd)
            except OSError:
                pass
        self._proc = None
        self._writer_fd = None
        self._session = None
