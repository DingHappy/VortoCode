"""Web 服务器绑定 fail-closed（2026-07 审计 P0#5）。

绑到非本地地址却没设 AUTODEV_API_TOKEN 时必须拒绝启动，而非只警告——否则忘设 token
就把所有 API（含 /ws 主 agent，可读写文件/跑命令）无鉴权暴露到网络。
"""

import pytest

from src.web.server import _insecure_bind_reason, start_server


def test_local_host_always_ok(monkeypatch):
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    for h in ("127.0.0.1", "localhost", "::1"):
        assert _insecure_bind_reason(h) == ""


def test_public_bind_without_token_refused(monkeypatch):
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTODEV_ALLOW_INSECURE_BIND", raising=False)
    for h in ("0.0.0.0", "192.168.1.10"):
        assert "拒绝启动" in _insecure_bind_reason(h)


def test_public_bind_with_token_ok(monkeypatch):
    monkeypatch.setenv("AUTODEV_API_TOKEN", "s3cret-strong-token")
    monkeypatch.delenv("AUTODEV_ALLOW_INSECURE_BIND", raising=False)
    assert _insecure_bind_reason("0.0.0.0") == ""


def test_public_bind_with_explicit_optout_ok(monkeypatch):
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    monkeypatch.setenv("AUTODEV_ALLOW_INSECURE_BIND", "1")
    assert _insecure_bind_reason("0.0.0.0") == ""


def test_start_server_raises_on_insecure_bind(monkeypatch):
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTODEV_ALLOW_INSECURE_BIND", raising=False)
    # 到不了 uvicorn.run 就该抛 SystemExit；真跑起来会阻塞，故此处只验证拒绝路径
    with pytest.raises(SystemExit):
        start_server(host="0.0.0.0", port=8080)
