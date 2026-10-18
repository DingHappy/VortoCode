"""桌面操作工具（实验功能）：离线测试，用假后端记录命令，不碰真实桌面。"""
import pytest

from src.agents.tools import desktop
from src.agents.trust import INTERACT

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class FakeBackend:
    label = "测试桌面"

    def __init__(self, *, png=PNG, fail=None):
        self.calls, self.png, self.fail = [], png, fail

    async def run(self, command, *, stdin=b""):
        self.calls.append((command, stdin))
        if self.fail and self.fail in " ".join(command):
            raise RuntimeError("目标机器上没有安装 mousepad")
        if command == desktop._SCREENSHOT:
            return self.png
        if command[:2] == ["xdotool", "getactivewindow"]:
            return b"Terminal - demo\n"
        if command == desktop._WINDOWS:
            return (b"12\tWINDOW=12 X=10 Y=20 WIDTH=300 HEIGHT=200 SCREEN=0\tEditor\n"
                    b"ACTIVE\tEditor\n")
        return b""


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv(desktop.APPS_ENV, "xfce4-terminal, mousepad, bad;app")
    asked = []
    answer = {"ok": True}

    async def confirm(message, kind=None):
        asked.append((kind, message))
        return answer["ok"]

    backend = FakeBackend()
    tools = {t.name: t for t in desktop.build_desktop_tools(str(tmp_path), confirm, backend=backend)}
    return tools, backend, asked, answer


def _actions(backend):
    return [cmd for cmd, _ in backend.calls if cmd != desktop._SCREENSHOT and cmd[:2] != ["xdotool", "getactivewindow"]]


# ── 什么时候装配 ──

@pytest.mark.parametrize("env,kind,ask,profile,expected", [
    ("1", "web", True, "local", True),
    ("", "web", True, "local", False),            # 默认关闭
    ("1", "im", True, "local", False),            # IM 入口不可信
    ("1", "cli", True, "local", False),
    ("1", "web", False, "local", False),          # 问不到人
    ("1", "web", True, "external", False),
    ("1", "web", True, "unattended", False),
])
def test_only_attended_local_web_sessions_get_desktop_control(monkeypatch, env, kind, ask, profile, expected):
    monkeypatch.setenv(desktop.ENABLE_ENV, env)
    assert desktop.desktop_control_enabled(kind, profile=profile, can_ask_human=ask) is expected


def test_build_session_wires_tools_only_when_enabled(tmp_path, monkeypatch):
    from src.gateway.agent_session import build_session

    async def human(_message):
        return True

    monkeypatch.delenv(desktop.ENABLE_ENV, raising=False)
    off = build_session(str(tmp_path), kind="web", confirm=human, can_ask_human=True, capability_profile="local")
    assert "desktop_click" not in off.tools
    monkeypatch.setenv(desktop.ENABLE_ENV, "1")
    on = build_session(str(tmp_path), kind="web", confirm=human, can_ask_human=True, capability_profile="local")
    assert {"desktop_screenshot", "desktop_click", "desktop_type"} <= set(on.tools)
    assert on.tools["desktop_screenshot"].untrusted_source and on.tools["desktop_click"].outward
    im = build_session(str(tmp_path), kind="im", confirm=human, can_ask_human=True, capability_profile="local")
    assert "desktop_click" not in im.tools


# ── 目标与参数安全 ──

def test_ssh_target_and_display_are_validated_and_text_never_hits_argv():
    backend = desktop.DesktopBackend("ssh:user@desktop-vm.lan", ":0")
    argv = backend.argv(["xdotool", "key", "--", "ctrl+l"])
    assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"] and argv[5] == "user@desktop-vm.lan"
    assert argv[6].startswith('env DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" xdotool key -- ctrl+l')
    for bad in ("ssh:host;rm -rf /", "ssh:", "ssh:a b@c", "telnet:host", "ssh:-oProxyCommand=x", "ssh:-v",
                "ssh:user@-v", "ssh:-user@host"):
        with pytest.raises(ValueError):
            desktop.DesktopBackend(bad, ":0")
    with pytest.raises(ValueError):
        desktop.DesktopBackend("local", ":0; rm")
    assert desktop.DesktopBackend("local", ":1").argv(["xdotool"]) == ["xdotool"]


# ── 只读 ──

async def test_screenshot_is_attached_as_an_image(setup, tmp_path):
    tools, _backend, asked, _ = setup
    result = await tools["desktop_screenshot"].handler({})
    path = result["images"][0]
    assert path.endswith(".png") and open(path, "rb").read() == PNG
    assert "不可信" in result["text"] and asked == []                     # 只读不问


async def test_non_png_screenshot_is_reported_as_failure(tmp_path):
    async def confirm(message, kind=None):
        return True
    tools = {t.name: t for t in desktop.build_desktop_tools(str(tmp_path), confirm, backend=FakeBackend(png=b"oops"))}
    result = await tools["desktop_screenshot"].handler({})
    assert result["ok"] is False and "PNG" in result["text"]


async def test_window_list(setup):
    tools, *_ = setup
    text = await tools["desktop_windows"].handler({})
    assert "[12] Editor  (x,y,w,h=10,20,300,200)" in text and "当前活动窗口：Editor" in text


# ── 写操作：每次都问，拒绝就不执行 ──

async def test_click_asks_with_window_and_coordinates_then_returns_a_fresh_screenshot(setup):
    tools, backend, asked, _ = setup
    result = await tools["desktop_click"].handler({"x": 100, "y": 200, "button": "right"})
    assert asked[0][0] == INTERACT and "右键点击坐标 (100, 200)" in asked[0][1] and "Terminal - demo" in asked[0][1]
    assert _actions(backend) == [["xdotool", "mousemove", "--sync", "100", "200", "click", "3"]]
    assert result["images"]


async def test_denied_action_runs_nothing(setup):
    tools, backend, _asked, answer = setup
    answer["ok"] = False
    assert await tools["desktop_click"].handler({"x": 1, "y": 1}) == "用户没有同意这次操作。"
    assert await tools["desktop_type"].handler({"text": "hello"}) == "用户没有同意这次操作。"
    assert _actions(backend) == []


async def test_type_goes_through_stdin_and_refuses_secrets(setup):
    tools, backend, asked, _ = setup
    await tools["desktop_type"].handler({"text": "你好; rm -rf ~"})
    [(command, stdin)] = [(c, s) for c, s in backend.calls if c[:2] == ["sh", "-c"] and c != desktop._SCREENSHOT]
    assert "rm -rf" not in " ".join(command) and stdin == "你好; rm -rf ~".encode()
    asked.clear()
    refused = await tools["desktop_type"].handler({"text": "token sk-abcdefghijklmnopqrstuvwxyz123456"})
    assert "不会替你输入" in refused and asked == []
    assert "最多" in await tools["desktop_type"].handler({"text": "x" * 3000})


@pytest.mark.parametrize("keys", ["ctrl+l; rm -rf /", "$(reboot)", "", "a b;c"])
async def test_key_names_are_validated(setup, keys):
    tools, backend, asked, _ = setup
    assert "按键名" in await tools["desktop_key"].handler({"keys": keys})
    assert asked == [] and _actions(backend) == []


async def test_valid_keys(setup):
    tools, backend, *_ = setup
    await tools["desktop_key"].handler({"keys": "ctrl+l Return"})
    assert _actions(backend) == [["xdotool", "key", "--", "ctrl+l", "Return"]]


async def test_open_only_allowlisted_apps_and_passes_the_name_as_an_argument(setup):
    tools, backend, asked, _ = setup
    assert "不在允许启动的应用里" in await tools["desktop_open"].handler({"app": "bash"})
    assert "不在允许启动的应用里" in await tools["desktop_open"].handler({"app": "bad;app"})   # 名单里非法项被丢弃
    assert asked == []
    await tools["desktop_open"].handler({"app": "xfce4-terminal"})
    [command] = _actions(backend)
    assert command[:2] == ["sh", "-c"] and command[-2:] == ["sh", "xfce4-terminal"]
    assert "command -v" in command[2] and "pgrep" in command[2]           # 先查装没装、后查进程在不在


async def test_open_reports_missing_app_honestly(tmp_path, monkeypatch):
    monkeypatch.setenv(desktop.APPS_ENV, "mousepad")

    async def confirm(message, kind=None):
        return True
    tools = {t.name: t for t in desktop.build_desktop_tools(str(tmp_path), confirm, backend=FakeBackend(fail="mousepad"))}
    result = await tools["desktop_open"].handler({"app": "mousepad"})
    assert result["ok"] is False and "没有安装" in result["text"]
