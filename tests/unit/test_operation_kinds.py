"""删除与外发的确认行为：最高授权也必须等待主人，拒绝时不执行。"""

import pytest

from src.agents import taint, trust
from src.agents.gate import make_confirm_gate
from src.agents.main_agent import build_agent_tools
from src.agents.tools.artifacts import ArtifactStore


@pytest.fixture(autouse=True)
def clean_taint():
    taint.reset_taint()
    yield
    taint.reset_taint()


@pytest.mark.parametrize("name", ["delete_artifact", "publish_artifact", "update_artifact", "open_pr"])
@pytest.mark.parametrize("answer", [False, True])
@pytest.mark.parametrize("auto_approve", [False, True])
async def test_full_trust_still_confirms_delete_publish_and_pr(
        tmp_path, monkeypatch, name, answer, auto_approve):
    from src.agents import vcs

    store = ArtifactStore(str(tmp_path))
    original = store.publish("原稿", "<p>原稿</p>")
    pushes, asked = [], []

    def push(*args, **kwargs):
        pushes.append(args)
        return {"ok": True, "url": "https://example.invalid/review/1"}

    async def ask(message, kind=None):
        asked.append(kind)
        assert store.meta(original["id"])["version"] == 1
        assert len(store.list()) == 1
        assert not pushes
        return answer

    monkeypatch.setattr(vcs, "push_and_open_pr", push)
    gate = make_confirm_gate(ask, trust_level=trust.FULL, capability_profile="local",
                             auto_approve=auto_approve, can_ask_human=True)
    tools = {tool.name: tool for tool in build_agent_tools(
        str(tmp_path), confirm=gate, with_artifacts=True)}
    args = {
        "delete_artifact": {"id": original["id"]},
        "publish_artifact": {"title": "新稿", "html": "<p>新稿</p>"},
        "update_artifact": {"id": original["id"], "html": "<p>更新</p>"},
        "open_pr": {"branch": "codex/example", "title": "示例"},
    }[name]
    await tools["publish_artifact" if name == "update_artifact" else name].handler(args)

    assert asked == [trust.IRREVERSIBLE if name == "delete_artifact" else trust.DELIVER]
    if name == "delete_artifact":
        assert store.exists(original["id"]) is not answer
    elif name == "publish_artifact":
        assert len(store.list()) == 1 + int(answer)
    elif name == "update_artifact":
        assert store.meta(original["id"])["version"] == 1 + int(answer)
    else:
        assert len(pushes) == int(answer)


async def test_irreversible_tool_inventory_is_nonempty(tmp_path):
    """真实调用工具收集不可撤回名单；漏申报或名单为空均失败。"""
    observed = {}
    current = None

    async def deny(message, kind=None):
        observed[current] = kind
        return False

    store = ArtifactStore(str(tmp_path))
    original = store.publish("原稿", "<p>原稿</p>")
    tools = {tool.name: tool for tool in build_agent_tools(
        str(tmp_path), confirm=deny, with_artifacts=True)}
    for name, args in {
        "delete_artifact": {"id": original["id"]},
        "publish_artifact": {"title": "新稿", "html": "<p>新稿</p>"},
        "open_pr": {"branch": "codex/example", "title": "示例"},
    }.items():
        current = name
        await tools[name].handler(args)
    irreversible_tools = {name for name, kind in observed.items() if kind == trust.IRREVERSIBLE}
    assert irreversible_tools
    assert irreversible_tools == {"delete_artifact"}
    assert store.exists(original["id"])


@pytest.mark.parametrize("name", ["send_image", "send_file", "send_document"])
@pytest.mark.parametrize("answer", [False, True])
async def test_full_trust_media_sends_wait_for_confirmation(tmp_path, monkeypatch, name, answer):
    from src.agents.tools.media import build_im_media_tools

    (tmp_path / "sample.txt").write_text("示例")
    asked, sent = [], []

    async def sender(*args):
        sent.append(args)
        return True

    async def human(message, kind=None):
        assert not sent
        asked.append(kind)
        return answer

    monkeypatch.setattr("src.agents.tools.media._OWNER_MEDIA_SENDER", sender)
    gate = make_confirm_gate(human, auto_approve=True, can_ask_human=True)
    tools = {tool.name: tool for tool in build_im_media_tools(str(tmp_path), confirm=gate)}
    args = {"title": "示例", "content": "正文"} if name == "send_document" else {"path": "sample.txt"}
    await tools[name].handler(args)
    assert asked == [trust.DELIVER]
    assert len(sent) == int(answer)
