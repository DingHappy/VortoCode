"""Bot account/mode namespaces and main-chat boundaries; no real IM or model traffic."""
import asyncio

import pytest

from src.gateway import im_runtime, im_service, main_conversation as main
from src.gateway.session_store import load_session
from src.im.bridge import IMBridge
from src.im.channel import ChannelEvent
from src.im.dingtalk import DingTalkAdapter
from src.im.telegram import TelegramAdapter
from tests.unit.test_im_bridge import FakeAdapter, ScriptedLLM
from tests.unit.test_im_in_gateway import FakeRunner


class AccountAdapter(FakeAdapter):
    def __init__(self, account="bot-a", **kwargs):
        super().__init__(**kwargs)
        self.account = account

    def conversation_identity(self):
        return self.account


def bridge(root, *, account="bot-a", owner="owner-1", conversation="isolated", adapter=None, **kwargs):
    return IMBridge(str(root), adapter or AccountAdapter(account), owner, channel="telegram",
                    conversation=conversation, llm=ScriptedLLM("本 bot 的结论"), **kwargs)


def persist(instance, text):
    instance.agent.history = [{"role": "user", "content": text}]
    instance._record("user", text, "synthetic")
    instance._activities = [{"type": "agent_tool", "name": text}]
    instance.mode = "build"
    instance._persist()


@pytest.mark.parametrize("change", ["account", "owner", "conversation"])
def test_history_transcript_mode_and_activities_do_not_cross_bot_identity(tmp_path, change):
    first = bridge(tmp_path)
    persist(first, "只属于甲的材料")
    options = {"account": "bot-b"} if change == "account" else {"owner": "owner-2"} if change == "owner" else {"conversation": "primary"}
    other = bridge(tmp_path, **options)
    assert other._sid != first._sid and not other.agent.history and other.mode == "plan"
    assert not getattr(other, "_transcript", []) and not getattr(other, "_activities", [])
    restored = bridge(tmp_path)
    assert restored.agent.history == first.agent.history and restored.mode == "build"
    assert restored._transcript == first._transcript and restored._activities == first._activities
    assert load_session(str(tmp_path), first._sid)["history"] == first.agent.history


@pytest.mark.parametrize("provider", ["telegram", "dingtalk"])
def test_provider_account_identity_survives_secret_rotation_but_not_account_change(tmp_path, provider):
    if provider == "telegram":
        adapters = [TelegramAdapter("111:old-secret", "42"), TelegramAdapter("111:new-secret", "42"), TelegramAdapter("222:other-secret", "42")]
    else:
        adapters = [DingTalkAdapter("app-a", "old-secret", "42"), DingTalkAdapter("app-a", "new-secret", "42"), DingTalkAdapter("app-b", "other-secret", "42")]
    instances = [IMBridge(str(tmp_path), a, "42", channel=provider, llm=ScriptedLLM("offline")) for a in adapters]
    persist(instances[0], "保留本账号历史")
    assert instances[0]._sid == instances[1]._sid != instances[2]._sid
    restored = IMBridge(str(tmp_path), adapters[1], "42", channel=provider, llm=ScriptedLLM("offline"))
    assert restored.agent.history == instances[0].agent.history
    for item in instances:
        assert "secret" not in item._sid and len(item._sid) < 80


def test_legacy_unattributed_history_is_preserved_but_not_adopted_by_a_provider_bot(tmp_path):
    legacy = bridge(tmp_path, adapter=FakeAdapter(), conversation="primary")
    persist(legacy, "归属未知的旧记录")
    before = load_session(str(tmp_path), legacy._sid)
    new = bridge(tmp_path, adapter=TelegramAdapter("111:fake-secret", "owner-1"), conversation="primary")
    assert new._sid != legacy._sid and not new.agent.history
    assert load_session(str(tmp_path), legacy._sid) == before


def test_isolated_persona_does_not_read_or_write_the_primary_or_other_bot_persona(tmp_path):
    primary = bridge(tmp_path, conversation="primary")
    primary._save_persona("主要助手的服务对象")
    first = bridge(tmp_path, workspace_scope="general")
    assert not first._load_persona()
    first._save_persona("只负责验收证据整理")
    restored = bridge(tmp_path, workspace_scope="general")
    assert "只负责验收证据整理" in restored.agent._system("plan")
    assert not bridge(tmp_path, account="bot-b")._load_persona()
    assert primary._load_persona() == "主要助手的服务对象"
    assert first._persona_path() != primary._persona_path()
    assert first._hello_stamp_path() != primary._hello_stamp_path()


async def test_isolated_turn_never_imports_or_writes_primary_chat_or_its_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "shared_turn_target", lambda root: "sid-primary")

    async def forbidden(*args, **kwargs):
        pytest.fail("isolated bot entered primary chat/digest")

    monkeypatch.setattr(main, "run_external_turn", forbidden)
    from src.gateway import turn_digest
    monkeypatch.setattr(turn_digest, "generate_digest", forbidden)
    monkeypatch.setattr(turn_digest, "digest_enabled", lambda: True)
    isolated = bridge(tmp_path, workspace_scope="general")
    assert not isolated._shares_main_chat()
    assert bridge(tmp_path, conversation="primary", workspace_scope="general")._shares_main_chat()
    await isolated._run_turn("独立的调查问题")
    await asyncio.sleep(0)  # Drain the existing optional digest task.
    assert [r["role"] for r in isolated._transcript] == ["user", "assistant"]
    assert any("独立的调查问题" in str(r) for r in isolated.agent.history)


@pytest.mark.parametrize("prefix", ["dsp-", "dpr-", "dgl-", "cdx-"])
async def test_isolated_entry_cannot_consume_foreign_durable_approvals(tmp_path, monkeypatch, prefix):
    isolated = bridge(tmp_path)

    async def forbidden(*args, **kwargs):
        pytest.fail("a specialist handled a primary approval")

    for method in ("_decide_dispatch", "_open_dispatch_pr", "_confirm_dispatch_goal", "_decide_codex_action"):
        monkeypatch.setattr(isolated, method, forbidden)
    await isolated._on_event(ChannelEvent(kind="callback", sender_id=isolated.owner_id, callback_id=prefix + "example", approved=True))
    local = asyncio.get_running_loop().create_future()
    isolated._pending["local"] = local
    await isolated._on_event(ChannelEvent(kind="callback", sender_id=isolated.owner_id, callback_id="local", approved=True))
    assert local.result() is True


async def test_isolated_entry_does_not_flush_global_notices_or_run_primary_commands(tmp_path, monkeypatch):
    isolated = bridge(tmp_path)
    from src.scheduling import notices

    async def forbidden(*args, **kwargs):
        pytest.fail("specialist entered primary notification/task flow")

    monkeypatch.setattr(notices, "flush_undelivered", forbidden)
    monkeypatch.setattr(isolated, "_submit_task", forbidden)
    assert await isolated.flush_pending_notices() == {"sent": 0, "dropped": 0, "kept": 0}
    for command in ("/task change", "/ok", "/mode build"):
        await isolated._handle_command(command)
    assert isolated.mode == "plan" and "总助手" in isolated.adapter.texts()[-1]
    persist(isolated, "要清掉的上下文")
    await isolated._handle_command("/new")
    assert not isolated.agent.history


def test_task_owner_and_turn_bindings_are_bound_to_the_bot_session(tmp_path, monkeypatch):
    from src.gateway import agent_session
    build, captured = agent_session.build_session, []

    def capture(*args, **kwargs):
        captured.append(kwargs)
        return build(*args, **kwargs)

    monkeypatch.setattr(agent_session, "build_session", capture)
    first, second = bridge(tmp_path, workspace_scope="general"), bridge(tmp_path, account="bot-b", workspace_scope="general")
    assert captured[0]["task_owner"]() == first._bindings.session == first._sid
    assert captured[1]["task_owner"]() == second._sid != first._sid
    assert all(row["untrusted_input"] is True and row["can_ask_human"] is True for row in captured)


@pytest.mark.parametrize("value", [None, [], True, "typo", ""])
def test_invalid_conversation_mode_never_falls_back_to_primary(tmp_path, value):
    with pytest.raises(ValueError, match="primary or isolated"):
        bridge(tmp_path, conversation=value)


def test_isolated_adapter_requires_a_stable_identity(tmp_path):
    with pytest.raises(ValueError, match="stable"):
        bridge(tmp_path, adapter=FakeAdapter())


@pytest.mark.parametrize("channel", ["../outside", "telegram/../../outside", ""])
def test_channel_cannot_escape_the_bot_persona_namespace(tmp_path, channel):
    with pytest.raises(ValueError, match="channel name"):
        IMBridge(str(tmp_path), AccountAdapter(), "owner-1", channel=channel, conversation="isolated")


def test_invalid_config_is_rejected_by_the_shared_embedded_and_standalone_parser(monkeypatch):
    monkeypatch.setenv("VORTOCODE_IM_CONVERSATION", "isloated")
    with pytest.raises(im_service.IMConfigError):
        im_service.load_conversation()
    monkeypatch.setenv("VORTOCODE_IM_CONVERSATION", "isolated")
    assert im_service.load_conversation() == "isolated"


async def test_embedded_isolated_bot_does_not_take_or_clear_primary_notification_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_IM_CONVERSATION", "isolated")
    monkeypatch.setenv("VORTOCODE_IM_ROLE", "interactive")
    sent, registered = [], []

    async def primary(text):
        sent.append(text)

    im_runtime.set_owner_notifier(primary)
    runner = FakeRunner()
    try:
        instance, _ = im_service.start_embedded("telegram", str(tmp_path), adapter=AccountAdapter(),
            owner="owner-1", runner=runner, register_worker=registered.append)
        assert instance.conversation == "isolated" and not registered and not runner.subs
        assert await im_runtime.notify_owner("still primary", relay=False)
        im_service.stop_embedded()
        im_service.stop_embedded()
        assert await im_runtime.notify_owner("after stop", relay=False)
        assert sent == ["still primary", "after stop"]
    finally:
        im_service.stop_embedded()
        im_runtime.set_owner_notifier(None)


def test_notification_only_role_cannot_be_combined_with_isolated_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_IM_CONVERSATION", "isolated")
    monkeypatch.setenv("VORTOCODE_IM_ROLE", "notify")
    with pytest.raises(im_service.IMConfigError, match="notification route"):
        im_service.start_embedded("telegram", str(tmp_path), adapter=AccountAdapter(), owner="owner-1",
                                  runner=FakeRunner(), register_worker=lambda worker: None)
