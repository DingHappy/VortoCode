"""多语言语义导航：通用 LSP 客户端（JSON-RPC over stdio）+ 语言服务器登记表。

jedi（src/agents/lsp.py）只懂 Python。本模块对接**真正的语言服务器二进制**（如
typescript-language-server / gopls / rust-analyzer），用 LSP 协议做 TS/JS… 的
go-to-definition / find-references / document-symbol，补上 jedi 覆盖不到的语言（缺口 A3）。

- 服务器未装 → `server_available` 为假，上层降级（提示装 / 回退 grep），绝不崩。
- 每次调用起一个一次性 server 子进程、查完即关（导航是低频工具，这点延迟可接受）。
- 协议封帧（Content-Length）抽成纯函数 encode_frame/decode_frames，便于在 CI 里确定性测
  （真·语言服务器的行为测则 skipif 未装）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

_SKIP_DIRS = {".git", "node_modules", ".vortocode", "dist", "build", ".next",
              "__pycache__", ".venv", "venv", "coverage", ".mypy_cache"}
_MAX_FILES = 60            # 为载入项目而 didOpen 的文件上限（防超大仓拖慢/吃内存）
_MAX_REFS = 40
_MAX_DEFS = 10

# 语言服务器登记表：扩展名 → 服务器。新增语言只要在这加一条 + 装好二进制。
_SERVERS: list[dict] = [
    {
        "name": "typescript-language-server",
        "cmd": ["typescript-language-server", "--stdio"],
        "exts": {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"},
        "language_ids": {".ts": "typescript", ".tsx": "typescriptreact",
                         ".js": "javascript", ".jsx": "javascriptreact",
                         ".mjs": "javascript", ".cjs": "javascript"},
    },
]

# LSP SymbolKind 数字 → 可读名（节选常用）。
_KIND = {5: "class", 6: "method", 7: "property", 8: "field", 9: "constructor",
         10: "enum", 11: "interface", 12: "function", 13: "variable",
         14: "constant", 23: "struct", 26: "type"}


# ----------------------------------------------------------------- 纯协议封帧
def encode_frame(obj: dict) -> bytes:
    """把一个 JSON-RPC 消息封成 LSP 帧：`Content-Length: N\\r\\n\\r\\n<json>`。"""
    data = json.dumps(obj).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n%s" % (len(data), data)


def decode_frames(buf: bytes) -> tuple[list[dict], bytes]:
    """从字节缓冲解出尽可能多的完整帧，返回 (消息列表, 剩余未完字节)。坏帧跳过、不抛。"""
    msgs: list[dict] = []
    while True:
        sep = buf.find(b"\r\n\r\n")
        if sep == -1:
            break
        length: Optional[int] = None
        for line in buf[:sep].split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    length = None
        if length is None:                 # 头里没有合法 Content-Length → 丢弃这段头继续
            buf = buf[sep + 4:]
            continue
        start = sep + 4
        if len(buf) - start < length:      # 帧体还没收全
            break
        body = buf[start:start + length]
        buf = buf[start + length:]
        try:
            msgs.append(json.loads(body))
        except (ValueError, UnicodeDecodeError):
            pass                            # 坏 JSON 跳过
    return msgs, buf


def path_to_uri(path: str) -> str:
    """本地路径 → file:// URI（跨平台）。

    用 `Path.as_uri()` 而不是 `urljoin("file:", pathname2url(...))`：Python 3.14 起
    `pathname2url` 会自带空 authority（`///private/tmp/a.ts`），urljoin 再拼就把它塌成
    `file:/private/tmp/a.ts`——少了两个斜杠，LSP server 认不出这个 URI。`as_uri()` 在各版本
    和各平台上都给 `file:///…`（Windows 下是 `file:///C:/…`），并且照样做百分号转义。
    """
    return Path(os.path.realpath(path)).as_uri()


def uri_to_path(uri: str) -> str:
    """file:// URI → 本地路径（够用版）。"""
    if uri.startswith("file://"):
        from urllib.parse import unquote, urlparse
        return unquote(urlparse(uri).path)
    return uri


# ----------------------------------------------------------------- 登记表/可用性
def server_for_ext(ext: str) -> Optional[dict]:
    """按扩展名取对应语言服务器配置；没有则 None。"""
    ext = ext.lower()
    return next((s for s in _SERVERS if ext in s["exts"]), None)


def server_available(server: dict) -> bool:
    """该语言服务器二进制是否在 PATH 上。"""
    return bool(server) and shutil.which(server["cmd"][0]) is not None


def language_id(server: dict, ext: str) -> str:
    return server.get("language_ids", {}).get(ext.lower(), "plaintext")


# ----------------------------------------------------------------- 通用 LSP 客户端
class LspClient:
    """最小同步 LSP 客户端：起子进程、读写帧、按 id 取响应。用作上下文管理器自动关停。"""

    def __init__(self, cmd: list[str], root_path: str, timeout: float = 20.0):
        # 第三方语言服务器二进制（typescript-language-server 等）过 child_env 起：剥掉
        # VortoCode 自己的操作密钥再交给它——语言服务器只需 PATH/HOME/locale/NODE_OPTIONS
        # （child_env 全保留），对那 6 个密钥零合法需求。否则密钥就明文躺在第三方进程 env 里。
        # 与 shell/mcp/hook/skill/worktree/self-improve/code-fix 同口径，收口 agent runtime
        # 一线 spawn 点里的 LSP 这一个。（沙箱 runner、集成终端 PTY 等另有执行面，另行评估收口。）
        from src.agents.sandbox import child_env
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0, env=child_env())
        self._root = os.path.realpath(root_path)
        self._timeout = timeout
        self._id = 0
        self._responses: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._buf = b""
        self._alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        fd = self._proc.stdout.fileno()
        while self._alive:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            self._buf += chunk
            msgs, self._buf = decode_frames(self._buf)
            for m in msgs:
                if "id" in m and ("result" in m or "error" in m):   # 我方请求的响应
                    with self._lock:
                        self._responses[m["id"]] = m
                # 服务器→客户端的请求/通知（configuration、progress…）一律忽略：只读查询不需应答

    def _send(self, obj: dict) -> None:
        try:
            self._proc.stdin.write(encode_frame(obj))
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            self._alive = False

    def request(self, method: str, params: Any, timeout: Optional[float] = None) -> Optional[dict]:
        with self._lock:
            self._id += 1
            i = self._id
        self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        deadline = time.time() + (timeout or self._timeout)
        while time.time() < deadline:
            with self._lock:
                if i in self._responses:
                    return self._responses.pop(i)
            if self._proc.poll() is not None:
                break
            time.sleep(0.01)
        return None

    def notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def initialize(self) -> bool:
        uri = path_to_uri(self._root)
        resp = self.request("initialize", {
            "processId": os.getpid(), "rootUri": uri, "capabilities": {},
            "workspaceFolders": [{"uri": uri, "name": Path(self._root).name}]})
        if resp is None or "result" not in resp:
            return False
        self.notify("initialized", {})
        return True

    def did_open(self, path: str, language_id_: str) -> None:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": path_to_uri(path), "languageId": language_id_, "version": 1, "text": text}})

    def close(self) -> None:
        self._alive = False
        try:
            self.request("shutdown", None, timeout=2)
            self.notify("exit", {})
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "LspClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ----------------------------------------------------------------- 高层导航
def _collect_files(repo_root: str, exts: set[str]) -> list[str]:
    """收集仓库内目标语言文件（剪枝噪音目录、封顶 _MAX_FILES）——为让 server 载入项目。"""
    found: list[str] = []
    for dirpath, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if os.path.splitext(f)[1].lower() in exts:
                found.append(os.path.join(dirpath, f))
                if len(found) >= _MAX_FILES:
                    return found
    return found


def _rel(path: str, repo_root: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(repo_root).resolve()))
    except (ValueError, OSError):
        return f"{path}(外部)"


def _line_text(path: str, line0: int) -> str:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
        if 0 <= line0 < len(lines):
            return lines[line0].strip()[:160]
    except OSError:
        pass
    return ""


def _open_project(client: LspClient, server: dict, files: list[str]) -> None:
    for fp in files:
        client.did_open(fp, language_id(server, os.path.splitext(fp)[1]))


def _workspace_symbol(client: LspClient, name: str, retries: int = 6) -> list[dict]:
    """workspace/symbol 查名字；项目刚载入可能先返回空，短轮询几次。只留精确同名。"""
    for _ in range(retries):
        resp = client.request("workspace/symbol", {"query": name})
        results = (resp or {}).get("result") or []
        exact = [s for s in results if s.get("name") == name]
        if exact:
            return exact
        time.sleep(0.4)
    return []


def lsp_find_definition(repo_root: str, symbol: str) -> Optional[str]:
    """用语言服务器按符号名找定义（当前支持 TS/JS）。无服务器/无结果返回 None（上层再降级）。"""
    server = next((s for s in _SERVERS), None)
    if not server or not server_available(server):
        return None
    files = _collect_files(repo_root, server["exts"])
    if not files:
        return None
    try:
        with LspClient(server["cmd"], repo_root) as client:
            if not client.initialize():
                return None
            _open_project(client, server, files)
            syms = _workspace_symbol(client, symbol)
            if not syms:
                return None
            out = [f"符号 `{symbol}` 的定义（{min(len(syms), _MAX_DEFS)} 处，LSP）："]
            for s in syms[:_MAX_DEFS]:
                loc = s.get("location", {})
                p = uri_to_path(loc.get("uri", ""))
                line0 = loc.get("range", {}).get("start", {}).get("line", 0)
                kind = _KIND.get(s.get("kind"), "")
                out.append(f"- {_rel(p, repo_root)}:{line0 + 1}  [{kind}] {s.get('name')}")
                ctx = _line_text(p, line0)
                if ctx:
                    out.append(f"    {ctx}")
            return "\n".join(out)
    except Exception:  # noqa: BLE001
        return None


def lsp_find_references(repo_root: str, symbol: str) -> Optional[str]:
    """用语言服务器找全项目引用（当前 TS/JS）。无服务器/无结果返回 None。"""
    server = next((s for s in _SERVERS), None)
    if not server or not server_available(server):
        return None
    files = _collect_files(repo_root, server["exts"])
    if not files:
        return None
    try:
        with LspClient(server["cmd"], repo_root) as client:
            if not client.initialize():
                return None
            _open_project(client, server, files)
            syms = _workspace_symbol(client, symbol)
            if not syms:
                return None
            loc = syms[0].get("location", {})
            uri = loc.get("uri", "")
            start = loc.get("range", {}).get("start", {})
            resp = client.request("textDocument/references", {
                "textDocument": {"uri": uri},
                "position": {"line": start.get("line", 0), "character": start.get("character", 0)},
                "context": {"includeDeclaration": True}})
            refs = (resp or {}).get("result") or []
            if not refs:
                return None
            defp = uri_to_path(uri)
            head = (f"符号 `{symbol}`（定义于 {_rel(defp, repo_root)}:{start.get('line', 0) + 1}，LSP）"
                    f"共 {len(refs)} 处引用"
                    + (f"，列前 {_MAX_REFS}：" if len(refs) > _MAX_REFS else "："))
            out = [head]
            for r in refs[:_MAX_REFS]:
                p = uri_to_path(r.get("uri", ""))
                line0 = r.get("range", {}).get("start", {}).get("line", 0)
                out.append(f"  {_rel(p, repo_root)}:{line0 + 1}: {_line_text(p, line0)}")
            return "\n".join(out)
    except Exception:  # noqa: BLE001
        return None


def _flatten_symbols(items: list[dict], depth: int = 0) -> list[tuple[int, int, str, int]]:
    """把 documentSymbol 结果摊平成 [(depth, line0, name, kind)]，兼容层级式与扁平式两种返回。"""
    out: list[tuple[int, int, str, int]] = []
    for it in items:
        rng = it.get("range") or it.get("location", {}).get("range") or {}
        line0 = rng.get("start", {}).get("line", 0)
        out.append((depth, line0, it.get("name", "?"), it.get("kind", 0)))
        for child in it.get("children") or []:
            out.extend(_flatten_symbols([child], depth + 1))
    return out


def lsp_document_symbols(repo_root: str, path: str) -> Optional[str]:
    """用语言服务器列文件结构大纲（当前 TS/JS）。无服务器/无结果返回 None。"""
    ext = os.path.splitext(path)[1].lower()
    server = server_for_ext(ext)
    if not server or not server_available(server):
        return None
    fp = Path(repo_root) / path
    if not fp.is_file():
        return None
    try:
        with LspClient(server["cmd"], repo_root) as client:
            if not client.initialize():
                return None
            client.did_open(str(fp), language_id(server, ext))
            time.sleep(0.3)
            resp = client.request("textDocument/documentSymbol",
                                  {"textDocument": {"uri": path_to_uri(str(fp))}})
            items = (resp or {}).get("result") or []
            if not items:
                return None
            flat = _flatten_symbols(items)
            out = [f"{path} 的结构（{len(flat)} 个符号，LSP）："]
            for depth, line0, name, kind in flat:
                indent = "  " * min(depth, 4)
                kname = _KIND.get(kind, "")
                out.append(f"  {indent}L{line0 + 1}: [{kname}] {name}")
            return "\n".join(out)
    except Exception:  # noqa: BLE001
        return None
