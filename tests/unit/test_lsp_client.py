"""多语言 LSP 客户端（src/agents/lsp_client.py）。

协议封帧 / 登记表 / URI / 符号摊平都确定性测（CI 也跑）；真·typescript-language-server
的行为测 skipif 未装（本地装了就验，CI 自动跳）。
"""
import os
import shutil

import pytest

from src.agents import lsp_client as lc

_HAS_TS = shutil.which("typescript-language-server") is not None


# ---- 纯协议封帧 ----
def test_encode_decode_roundtrip():
    frame = lc.encode_frame({"jsonrpc": "2.0", "id": 1, "method": "x", "params": {}})
    assert frame.startswith(b"Content-Length: ")
    msgs, rest = lc.decode_frames(frame)
    assert len(msgs) == 1 and msgs[0]["id"] == 1 and rest == b""


def test_decode_multiple_and_partial():
    a = lc.encode_frame({"id": 1, "result": 1})
    b = lc.encode_frame({"id": 2, "result": 2})
    msgs, rest = lc.decode_frames(a + b[:6])          # 完整 a + 半个 b
    assert [m["id"] for m in msgs] == [1] and rest == b[:6]
    msgs2, rest2 = lc.decode_frames(rest + b[6:])     # 续上 b 的后半
    assert [m["id"] for m in msgs2] == [2] and rest2 == b""


def test_decode_incomplete_header_waits():
    msgs, rest = lc.decode_frames(b"Content-Length: 10\r\n")   # 头还没收完
    assert msgs == [] and rest == b"Content-Length: 10\r\n"


def test_decode_skips_header_without_length():
    buf = b"X-Foo: bar\r\n\r\n" + lc.encode_frame({"id": 9, "result": 0})
    msgs, rest = lc.decode_frames(buf)
    assert [m["id"] for m in msgs] == [9] and rest == b""       # 无 Content-Length 的头被跳过


def test_decode_bad_json_skipped():
    bad = b"Content-Length: 3\r\n\r\n{x}" + lc.encode_frame({"id": 7, "result": 1})
    msgs, _ = lc.decode_frames(bad)
    assert [m["id"] for m in msgs] == [7]                       # 坏 JSON 跳过、不影响后续


# ---- 登记表 / 可用性 / languageId ----
def test_server_for_ext():
    assert lc.server_for_ext(".ts")["name"] == "typescript-language-server"
    assert lc.server_for_ext(".TSX")["name"] == "typescript-language-server"   # 大小写不敏感
    assert lc.server_for_ext(".py") is None
    assert lc.server_for_ext(".rs") is None


def test_server_available(monkeypatch):
    srv = lc.server_for_ext(".ts")
    monkeypatch.setattr(lc.shutil, "which", lambda _n: None)
    assert lc.server_available(srv) is False
    monkeypatch.setattr(lc.shutil, "which", lambda _n: "/usr/local/bin/tsls")
    assert lc.server_available(srv) is True
    assert lc.server_available(None) is False                  # 没服务器配置 → 假


def test_language_id():
    srv = lc.server_for_ext(".tsx")
    assert lc.language_id(srv, ".tsx") == "typescriptreact"
    assert lc.language_id(srv, ".ts") == "typescript"
    assert lc.language_id(srv, ".js") == "javascript"
    assert lc.language_id(srv, ".zzz") == "plaintext"


def test_uri_path_roundtrip(tmp_path):
    p = tmp_path / "a.ts"
    p.write_text("x", encoding="utf-8")
    uri = lc.path_to_uri(str(p))
    assert uri.startswith("file://")
    assert lc.uri_to_path(uri) == os.path.realpath(str(p))


def test_flatten_symbols_hierarchical():
    items = [{"name": "Foo", "kind": 5, "range": {"start": {"line": 4}},
              "children": [{"name": "bar", "kind": 6, "range": {"start": {"line": 5}}}]}]
    flat = lc._flatten_symbols(items)
    assert flat[0] == (0, 4, "Foo", 5)
    assert flat[1] == (1, 5, "bar", 6)                         # 子符号 depth+1


def test_flatten_symbols_flat_form():
    items = [{"name": "g", "kind": 12, "location": {"range": {"start": {"line": 0}}}}]
    assert lc._flatten_symbols(items)[0] == (0, 0, "g", 12)    # 兼容扁平 SymbolInformation


# ---- 真·语言服务器行为（需 typescript-language-server，CI 自动 skip）----
@pytest.mark.skipif(not _HAS_TS, reason="需要 typescript-language-server")
def test_ts_find_definition_and_references(tmp_path):
    (tmp_path / "a.ts").write_text('export function greet(n: string){ return "hi " + n; }\n',
                                   encoding="utf-8")
    (tmp_path / "b.ts").write_text('import {greet} from "./a";\ngreet("x");\ngreet("y");\n',
                                   encoding="utf-8")
    d = lc.lsp_find_definition(str(tmp_path), "greet")
    assert d and "a.ts:1" in d and "function" in d
    r = lc.lsp_find_references(str(tmp_path), "greet")
    assert r and "a.ts:1" in r and "b.ts:2" in r and "b.ts:3" in r   # 定义 + 两处调用


@pytest.mark.skipif(not _HAS_TS, reason="需要 typescript-language-server")
def test_ts_document_symbols(tmp_path):
    (tmp_path / "a.ts").write_text("export class Box { open(){ return 1; } }\n"
                                   "export function f(){ return 0; }\n", encoding="utf-8")
    out = lc.lsp_document_symbols(str(tmp_path), "a.ts")
    assert out and "Box" in out and "open" in out and "f" in out


@pytest.mark.skipif(not _HAS_TS, reason="需要 typescript-language-server")
def test_dispatch_through_public_lsp(tmp_path):
    from src.agents.lsp import document_symbols, find_definition
    (tmp_path / "a.ts").write_text("export function greet(n: string){ return n; }\n",
                                   encoding="utf-8")
    assert "a.ts:1" in find_definition(str(tmp_path), "greet")   # 公开 API 自动派发到 LSP
    assert "greet" in document_symbols(str(tmp_path), "a.ts")
