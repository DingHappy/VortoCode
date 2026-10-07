"""浏览器操控：受限站点 / 内网拒绝、点击输入必问、密码框不填、开关只在 Desktop Web 会话生效。"""
import pytest

from src.agents.gate import make_confirm_gate
from src.agents.tools.browser import browser_control_enabled, build_browser_tools, restricted_reason
from src.agents.trust import FULL, INTERACT, pre_authorized


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    # 离线：把 SSRF 判定换成"只拦 IP 字面量内网"，不碰真实 DNS。
    from src.agents import web_fetch
    monkeypatch.setattr(web_fetch, "refusal_reason",
                        lambda host: "内网" if host.startswith(("10.", "192.168.", "127.")) or host == "localhost" else "")


def test_restricted_sites_and_private_hosts():
    assert restricted_reason("https://example.com/page") == ""
    assert "受限站点" in restricted_reason("https://www.paypal.com/checkout")
    assert "受限站点" in restricted_reason("https://mail.google.com/")
    assert "银行" in restricted_reason("https://online.somebank.example/")
    assert "内网" in restricted_reason("http://192.168.1.1/")
    assert "http/https" in restricted_reason("file:///etc/passwd")
    assert "http/https" in restricted_reason("javascript:alert(1)")


def test_user_can_add_but_not_remove_blocked_domains(monkeypatch):
    monkeypatch.setenv("VORTOCODE_BROWSER_BLOCKED_DOMAINS", "intranet.example.com, .corp.example")
    assert restricted_reason("https://wiki.intranet.example.com/")
    assert restricted_reason("https://a.corp.example/")
    assert restricted_reason("https://paypal.com/")


def test_interact_is_never_pre_authorized():
    assert not pre_authorized(FULL, INTERACT)


def test_enabled_only_for_desktop_web_with_opt_in(monkeypatch):
    monkeypatch.delenv("VORTOCODE_ENABLE_BROWSER_CONTROL", raising=False)
    monkeypatch.setenv("VORTOCODE_DESKTOP_SIDECAR", "1")
    assert not browser_control_enabled("web")
    monkeypatch.setenv("VORTOCODE_ENABLE_BROWSER_CONTROL", "1")
    assert browser_control_enabled("web")
    assert not browser_control_enabled("im") and not browser_control_enabled("cli")
    monkeypatch.delenv("VORTOCODE_DESKTOP_SIDECAR")
    assert not browser_control_enabled("web")


class _FakeBrowser:
    def __init__(self):
        self.running = False
        self.url = "about:blank"
        self.clicks, self.typed = [], []
        self.element = {"x": 10, "y": 20, "tag": "button", "type": "", "label": "提交", "editable": False}

    async def start(self):
        self.running = True

    async def navigate(self, url):
        self.url = url

    async def wait_ready(self):
        pass

    async def evaluate(self, expression):
        if expression == "location.href":
            return self.url
        if "data-vorto-ref=\"${ref}\"" in expression:
            return self.element
        return {"title": "T", "url": self.url, "text": "正文", "items": ["[1] <button> 提交"]}

    async def click_at(self, x, y):
        self.clicks.append((x, y))

    async def insert_text(self, text):
        self.typed.append(text)

    async def press_enter(self):
        pass

    async def screenshot(self):
        return b"png"


def _tools(tmp_path, confirm, fake):
    return {t.name: t for t in build_browser_tools(str(tmp_path), confirm, browser_factory=lambda: fake)}


async def test_click_asks_even_at_full_trust_and_respects_no(tmp_path):
    asked = []

    async def ask(message):
        asked.append(message)
        return False

    gate = make_confirm_gate(ask, can_ask_human=True, trust_level=FULL, capability_profile="local")
    fake = _FakeBrowser()
    tools = _tools(tmp_path, gate, fake)
    assert "正文" in await tools["browser_open"].handler({"url": "https://example.com/"})
    result = await tools["browser_click"].handler({"ref": 1})
    assert asked and "example.com" in asked[0] and "提交" in asked[0]
    assert fake.clicks == [] and "没有同意" in result


async def test_type_refuses_password_and_types_after_yes(tmp_path):
    async def yes(message, kind=None):
        assert kind == INTERACT
        return True

    fake = _FakeBrowser()
    tools = _tools(tmp_path, yes, fake)
    await tools["browser_open"].handler({"url": "https://example.com/"})
    fake.element = {**fake.element, "tag": "input", "type": "password", "editable": True}
    assert "密码" in await tools["browser_type"].handler({"ref": 1, "text": "secret"})
    assert fake.typed == []
    fake.element = {**fake.element, "type": "text"}
    assert "已输入" in await tools["browser_type"].handler({"ref": 1, "text": "hello"})
    assert fake.typed == ["hello"]


async def test_restricted_open_never_starts_browser_and_redirects_are_stopped(tmp_path):
    async def yes(message, kind=None):
        return True

    fake = _FakeBrowser()
    tools = _tools(tmp_path, yes, fake)
    assert "受限站点" in await tools["browser_open"].handler({"url": "https://paypal.com/"})
    assert not fake.running
    await tools["browser_open"].handler({"url": "https://example.com/"})
    fake.url = "https://accounts.google.com/signin"         # 页面自己跳去了账号中心
    result = await tools["browser_click"].handler({"ref": 1})
    assert "已停止" in result and fake.url == "about:blank" and fake.clicks == []


def test_page_reading_tools_taint_the_turn():
    tools = build_browser_tools("/tmp", None, browser_factory=_FakeBrowser)
    assert all(t.untrusted_source and t.external_content for t in tools)
    assert {t.name for t in tools if not t.read_only} == {"browser_click", "browser_type"}
