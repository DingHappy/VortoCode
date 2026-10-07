"""Telegram owner pairing is offline-tested through an injected Bot API."""

import re
import stat
import os

import pytest

from src.gateway.im_service import build_adapter
from src.im.bridge import IMBridge
from src.im.channel import ChannelEvent
from src.im.telegram_pairing import PairingError, load_paired_owner, pair_telegram
from src.im.telegram import TelegramAdapter
from tests.unit.test_im_bridge import FakeAdapter, ScriptedLLM


def _message(update_id, sender, text, chat_type="private", chat_id=None):
    return {"update_id": update_id, "message": {
        "from": {"id": sender}, "chat": {"id": chat_id or sender, "type": chat_type},
        "text": text}}


@pytest.mark.asyncio
async def test_private_one_time_pairing_persists_owner_without_token(tmp_path, monkeypatch):
    shown = []
    calls = []
    batches = []

    async def request(method, payload):
        calls.append((method, payload))
        if method == "getMe":
            return {"id": 123, "username": "testbot"}
        if method == "getUpdates":
            return batches.pop(0) if batches else []
        return {"message_id": 1}

    def show(message):
        shown.append(message)
        code = re.search(r"/bind (\S+)", message).group(1)
        batches.append([_message(1, 55, f"/bind {code}", "group")])
        batches.append([_message(2, 55, "/bind wrong"),
                        _message(3, 77, f"/bind {code}")])

    owner = await pair_telegram("123:fake-secret", state_dir=tmp_path,
                                request_fn=request, show_code=show)
    assert owner == "77"
    assert load_paired_owner("123:rotated-secret", tmp_path) == "77"
    state_file = tmp_path / "telegram-123.json"
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert "fake-secret" not in state_file.read_text()
    assert [m for m, _ in calls].count("sendMessage") == 1
    assert [p["chat_id"] for m, p in calls if m == "sendMessage"] == ["77"]
    assert [p["offset"] for m, p in calls if m == "getUpdates"] == [0, 2, 4]
    assert shown and "10 分钟" in shown[0]
    with pytest.raises(PairingError, match="已有主人"):
        await pair_telegram("123:rotated-secret", state_dir=tmp_path, request_fn=request)


@pytest.mark.asyncio
async def test_group_and_wrong_chat_cannot_claim_pairing(tmp_path):
    shown = []

    async def request(method, payload):
        if method == "getMe":
            return {"id": 123}
        code = re.search(r"/bind (\S+)", shown[0]).group(1)
        return [_message(1, 7, f"/bind {code}", chat_id=99)]

    with pytest.raises(PairingError, match="过期"):
        await pair_telegram("123:x", state_dir=tmp_path, request_fn=request,
                            lifetime=0.01, show_code=shown.append)
    assert load_paired_owner("123:x", tmp_path) == ""


@pytest.mark.asyncio
async def test_bot_identity_mismatch_does_not_open_pairing(tmp_path):
    async def request(method, payload):
        return {"id": 999}

    with pytest.raises(PairingError, match="Bot 身份"):
        await pair_telegram("123:x", state_dir=tmp_path, request_fn=request)
    assert list(tmp_path.iterdir()) == []


def test_pairing_state_rejects_bad_permissions_and_symlink(tmp_path):
    path = tmp_path / "telegram-123.json"
    path.write_text('{"version":1,"bot_id":"123","owner_id":"7"}')
    path.chmod(0o644)
    with pytest.raises(PairingError, match="权限过宽"):
        load_paired_owner("123:x", tmp_path)
    path.chmod(0o600)
    assert load_paired_owner("123:x", tmp_path) == "7"
    path.rename(tmp_path / "real.json")
    path.symlink_to(tmp_path / "real.json")
    with pytest.raises(PairingError, match="普通文件"):
        load_paired_owner("123:x", tmp_path)


def test_build_adapter_loads_paired_owner(monkeypatch, tmp_path):
    import src.im.telegram_pairing as pairing

    pairing._save_owner("123:x", "42", tmp_path)
    monkeypatch.setattr(pairing, "_pairing_dir", lambda state_dir=None: tmp_path)
    monkeypatch.setenv("VORTOCODE_TG_TOKEN", "123:x")
    monkeypatch.delenv("VORTOCODE_TG_OWNER_ID", raising=False)
    adapter, owner = build_adapter("telegram")
    assert owner == "42" and adapter.owner_id == "42"


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o777])
def test_owner_load_rejects_unsafe_directory_even_with_private_file(tmp_path, mode):
    import src.im.telegram_pairing as pairing

    pairing._save_owner("123:x", "42", tmp_path)
    tmp_path.chmod(mode)
    try:
        with pytest.raises(PairingError, match="目录权限过宽"):
            load_paired_owner("123:x", tmp_path)
    finally:
        tmp_path.chmod(0o700)


def test_owner_load_rejects_symlink_directory(tmp_path):
    import src.im.telegram_pairing as pairing

    directory = tmp_path / "real"
    pairing._save_owner("123:x", "42", directory)
    link = tmp_path / "link"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(PairingError, match="普通目录"):
        load_paired_owner("123:x", link)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX file ownership")
def test_owner_load_rejects_directory_owned_by_another_account(tmp_path, monkeypatch):
    import src.im.telegram_pairing as pairing

    pairing._save_owner("123:x", "42", tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: tmp_path.stat().st_uid + 1)
    with pytest.raises(PairingError, match="当前系统用户"):
        load_paired_owner("123:x", tmp_path)


@pytest.mark.asyncio
async def test_unicode_wrong_code_does_not_abort_valid_pairing(tmp_path):
    batches = []

    async def request(method, payload):
        if method == "getMe":
            return {"id": 123}
        if method == "getUpdates":
            return batches.pop(0) if batches else []
        return {}

    def show(message):
        code = re.search(r"/bind (\S+)", message).group(1)
        batches.append([_message(1, 77, "/bind 错误码"),
                        _message(2, 42, f"/bind {code}")])

    assert await pair_telegram("123:x", state_dir=tmp_path, request_fn=request,
                               show_code=show) == "42"


@pytest.mark.asyncio
async def test_telegram_allowlist_cannot_route_other_users_into_owner_session(tmp_path):
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, "42", channel="telegram",
                      allow_from=["42", "77"], llm=ScriptedLLM("must not run"))
    await bridge._on_event(ChannelEvent(kind="message", sender_id="77", text="hello"))
    assert bridge._turn_task is None
    assert bridge._ignored == 1
    assert not adapter.sent


@pytest.mark.asyncio
async def test_unpaired_sender_attachment_is_not_downloaded(tmp_path):
    calls = []
    updates = [_message(1, 77, "", chat_type="private")]
    updates[0]["message"]["document"] = {"file_id": "stranger-file", "file_name": "x.txt"}

    async def request(method, payload):
        calls.append(method)
        return updates if method == "getUpdates" else {}

    adapter = TelegramAdapter("123:x", "42", request_fn=request,
                              inbox_dir=str(tmp_path / "inbox"))
    async for event in adapter.poll():
        assert event.sender_id == "77" and event.files == []
        break
    assert "getFile" not in calls
    assert not (tmp_path / "inbox").exists()


@pytest.mark.asyncio
async def test_attachment_error_does_not_echo_bot_token(tmp_path):
    async def request(method, payload):
        raise RuntimeError("https://api.telegram.org/bot123:secret/getFile")

    adapter = TelegramAdapter("123:secret", "42", request_fn=request)
    _images, _files, note = await adapter._fetch_attachments(
        [("file", "x", "x.txt", 10)])
    assert "123:secret" not in note
    assert "[redacted]" in note
