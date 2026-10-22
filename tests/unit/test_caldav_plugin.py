"""日历写入插件：每次确认（任何档位都不免）、只新建不覆盖、密码不外露、只跟同站点地址、时间与全天日程换算。"""
import base64
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import src.plugins as plugins
from src.agents import web_fetch
from src.agents.trust import INTERACT
from src.plugins import caldav

READY = plugins.plugin_ready
TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=TZ)
PASSWORD = "abcd-efgh-ijkl-mnop"


def multistatus(*responses):
    body = "".join(f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{prop}</d:prop>"
                   f"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>" for href, prop in responses)
    return (f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            f"{body}</d:multistatus>").encode()


CAL = '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>'


class Server:
    """仿 iCloud：主体在 caldav.icloud.com，日历主目录在 p01-caldav.icloud.com。"""

    def __init__(self, home="https://p01-caldav.icloud.com/123/calendars/", status=None):
        self.home, self.status, self.calls = home, status, []

    def __call__(self, url, *, headers=None, data=None, method=None, **_kw):
        self.calls.append({"url": url, "method": method, "headers": dict(headers or {}), "data": data})
        if self.status:
            raise web_fetch.FetchError(f"对方返回 HTTP {self.status}", self.status)
        if method == "PUT":
            return b""
        if b"current-user-principal" in data:
            return multistatus(("/", "<d:current-user-principal><d:href>/123/principal/</d:href></d:current-user-principal>"))
        if b"calendar-home-set" in data:
            return multistatus(("/123/principal/", f"<c:calendar-home-set><d:href>{self.home}</d:href></c:calendar-home-set>"))
        return multistatus(
            ("/123/calendars/", "<d:resourcetype><d:collection/></d:resourcetype>"),
            ("/123/calendars/tasks/", f'{CAL}<d:displayname>提醒事项</d:displayname>'
                                      '<c:supported-calendar-component-set><c:comp name="VTODO"/></c:supported-calendar-component-set>'),
            ("/123/calendars/home/", f'{CAL}<d:displayname>家庭</d:displayname>'
                                     '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>'),
            ("/123/calendars/work", f"{CAL}<d:displayname>工作</d:displayname>"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(plugins, "plugin_ready", READY)
    caldav._discovered.clear()
    folder = tmp_path / ".vortocode" / "connectors"
    folder.mkdir(parents=True)
    path = folder / "caldav.token"
    path.write_text(caldav.validate(f"me@icloud.com:{PASSWORD}") + "\n")
    os.chmod(path, 0o600)
    yield path
    caldav._discovered.clear()


@pytest.fixture
def server(monkeypatch):
    fake = Server()
    monkeypatch.setattr(web_fetch, "fetch_bytes", fake)
    monkeypatch.setattr(caldav, "_local_now", lambda: NOW)
    return fake


class Asker(list):
    def __init__(self, answer=True):
        super().__init__()
        self.answer = answer

    async def __call__(self, message, kind=None):
        self.append((message, kind))
        return self.answer


def add_tool(confirm):
    return {t.name: t for t in caldav.build_write_tools(confirm)}["calendar_add"]


def test_credential_forms():
    assert caldav.parse_credential(f"me@icloud.com:{PASSWORD}") == caldav.Account(
        "me@icloud.com", PASSWORD, "https://caldav.icloud.com/")
    account = caldav.parse_credential(caldav.validate("https://u%40x.com:p%2Fw@dav.example.com/dav/#%E5%B7%A5%E4%BD%9C"))
    assert account == caldav.Account("u@x.com", "p/w", "https://dav.example.com/dav/", "工作")
    for bad in (f"me@qq.com:{PASSWORD}", "http://u:p@dav.example.com/", "me@icloud.com", "https://dav.example.com/"):
        with pytest.raises(ValueError) as error:
            caldav.validate(bad)
        assert PASSWORD not in str(error.value)


def test_event_times_all_day_escaping_and_folding():
    event = caldav.build_event("看牙医", "2026-10-23 15:00", now=NOW)
    assert "DTSTART:20261023T070000Z" in event["ics"] and "DTEND:20261023T080000Z" in event["ics"]
    assert caldav.event_preview(event) == "在你的日历里新建日程？\n2026-10-23（周五）15:00–16:00\n看牙医"
    trip = caldav.build_event("出差", "2026-10-26", "2026-10-28", location="上海, 浦东; 3 楼", now=NOW)
    assert "DTSTART;VALUE=DATE:20261026" in trip["ics"] and "DTEND;VALUE=DATE:20261029" in trip["ics"]
    assert "LOCATION:上海\\, 浦东\\; 3 楼" in trip["ics"] and "10-26（周一） 到 10-28（周三）全天" in caldav.event_preview(trip)
    long = caldav.build_event("很长的标题" * 20, "2026-10-23 15:00", notes="第一行\n第二行", now=NOW)
    assert all(len(line.encode()) <= 75 for line in long["ics"].split("\r\n"))
    assert "DESCRIPTION:第一行\\n第二行" in long["ics"].replace("\r\n ", "")
    for args in (("", "2026-10-23 15:00"), ("x", "明天下午"), ("x", "2026-10-21 15:00"),
                 ("x", "2026-10-23 15:00", "2026-10-23 14:00"), ("x", "2026-10-23", "2026-10-24 10:00"),
                 ("x", "2026-10-23", "2026-11-23")):
        with pytest.raises(caldav.CalDAVError):
            caldav.build_event(*args, now=NOW)


async def test_add_asks_every_time_then_creates_without_overwriting(home, server):
    asker = Asker()
    out = await add_tool(asker).handler({"title": "看牙医", "start": "2026-10-23 15:00", "location": "口腔医院"})
    assert asker == [("在你的日历里新建日程？\n2026-10-23（周五）15:00–16:00\n看牙医\n地点：口腔医院", INTERACT)]
    put = server.calls[-1]
    assert put["method"] == "PUT" and put["headers"]["If-None-Match"] == "*"
    assert put["url"].startswith("https://p01-caldav.icloud.com/123/calendars/home/") and put["url"].endswith("@vortocode.ics")
    assert b"SUMMARY:\xe7\x9c\x8b\xe7\x89\x99\xe5\x8c\xbb" in put["data"]
    assert base64.b64decode(put["headers"]["Authorization"].split()[1]).decode() == f"me@icloud.com:{PASSWORD}"
    assert [c["method"] for c in server.calls] == ["PROPFIND", "PROPFIND", "PROPFIND", "PUT"]
    assert out == "已在日历「家庭」里建好：2026-10-23（周五）15:00–16:00 看牙医。"
    await add_tool(Asker()).handler({"title": "复诊", "start": "2026-10-30 15:00"})
    assert [c["method"] for c in server.calls][4:] == ["PUT"]                    # 找日历的结果缓存一小时


async def test_named_calendar_and_check(home, server):
    home.write_text(caldav.validate(f"me@icloud.com:{PASSWORD}#工作") + "\n")
    assert await caldav.check() == "能写入日历「工作」（只会在你确认后新建日程）"
    await add_tool(Asker()).handler({"title": "周会", "start": "2026-10-23 10:00"})
    assert server.calls[-1]["url"].startswith("https://p01-caldav.icloud.com/123/calendars/work/")
    caldav._discovered.clear()
    home.write_text(caldav.validate(f"me@icloud.com:{PASSWORD}#没有这个") + "\n")
    out = await add_tool(Asker()).handler({"title": "周会", "start": "2026-10-23 10:00"})
    assert "没找到名叫「没有这个」的日历" in out and "家庭" in out and "提醒事项" not in out


async def test_declined_bad_input_other_site_and_errors_never_write(home, server, monkeypatch):
    assert await add_tool(Asker(answer=False)).handler({"title": "x", "start": "2026-10-23 15:00"}) == "用户没有同意，日程没建。"
    asker = Asker()
    assert "时间要写成" in await add_tool(asker).handler({"title": "x", "start": "下周"})
    assert asker == [] and server.calls == []
    server.home = "https://evil.example.com/steal/"
    out = await add_tool(Asker()).handler({"title": "x", "start": "2026-10-23 15:00"})
    assert out == "日历服务器给了别的站点的地址，没有跟过去"
    assert all("evil" not in call["url"] for call in server.calls)
    caldav._discovered.clear()
    server.status = 401
    out = await add_tool(Asker()).handler({"title": "x", "start": "2026-10-23 15:00"})
    assert out == "日历服务器：账号或 App 专用密码不对" and PASSWORD not in out
    assert not any(call["method"] == "PUT" for call in server.calls)


async def test_session_attaches_add_tool_that_fails_closed_without_a_human(home, server):
    from src.gateway.agent_session import build_session

    class LLM:
        async def chat(self, messages, **kwargs):
            return {"content": "ok"}

    agent = build_session(str(home.parent), kind="im", workspace_scope="general", llm=LLM(), auto_approve=True)
    assert "calendar_add" in agent.tools and "calendar_add" in agent._system("plan")
    out = await agent.tools["calendar_add"].handler({"title": "x", "start": "2026-10-23 15:00"})
    assert out == "用户没有同意，日程没建。" and server.calls == []


def test_fetch_bytes_sends_the_requested_method(monkeypatch):
    seen = []

    class Response:
        def read(self, size):
            return b"ok"

    monkeypatch.setattr(web_fetch, "_host_is_safe", lambda host: True)
    monkeypatch.setattr(web_fetch, "_urlopen", lambda req, timeout: seen.append(req.get_method()) or Response())
    web_fetch.fetch_bytes("https://dav.example.com/", headers={"Depth": "0"}, data=b"<x/>", method="PROPFIND")
    web_fetch.fetch_bytes("https://dav.example.com/", data=b"x")
    assert seen == ["PROPFIND", "POST"]
