"""日历写入插件（CalDAV）：主人逐次确认后，在自己的日历里新建一条日程。

凭据是 `邮箱:App 专用密码`（iCloud 自动补服务器），或 `https://账号:密码@服务器[/路径][#日历名]`。
放在 `~/.vortocode/connectors/caldav.token`，不进配置、系统提示、日志或工具结果。

- 只做一件事：新建日程（PUT 一条新的 VEVENT，带 If-None-Match: *，不会覆盖已有的）。不改、不删。
- 每次都过确认门（INTERACT：任何授权档位都不免），问不到人的会话直接拒绝。
- 查日程仍走只读的日历订阅插件（calendar）；两者凭据分开，只想读的人不必给写权限。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urljoin, urlsplit

logger = logging.getLogger(__name__)

NAME = "caldav"
_SERVERS = {"icloud.com": "https://caldav.icloud.com/", "me.com": "https://caldav.icloud.com/",
            "mac.com": "https://caldav.icloud.com/"}
_NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
_CACHE_SECONDS = 3600
_MAX_RESPONSE = 2_000_000
_WEEKDAY_CN = "一二三四五六日"
_discovered: Dict[str, Tuple[float, "Calendar"]] = {}


class CalDAVError(Exception):
    """说明里不带密码。"""


@dataclass(frozen=True)
class Account:
    user: str
    password: str
    url: str
    calendar: str = ""


@dataclass(frozen=True)
class Calendar:
    url: str
    name: str


def parse_credential(value: str) -> Account:
    value = value.strip()
    value, _, wanted = value.partition("#")
    wanted = unquote(wanted).strip()
    if "://" not in value:
        user, sep, password = value.partition(":")
        domain = user.rpartition("@")[2].lower()
        if not sep or "@" not in user or not password:
            raise CalDAVError("要填「邮箱:App 专用密码」，或 https://账号:密码@服务器")
        if domain not in _SERVERS:
            raise CalDAVError("认不出这个邮箱的日历服务器，请写成 https://账号:密码@服务器")
        return Account(user, password, _SERVERS[domain], wanted)
    parts = urlsplit(value)
    if parts.scheme.lower() != "https":
        raise CalDAVError("只支持加密连接（https://）")
    userinfo, _, host = parts.netloc.rpartition("@")
    user, sep, password = userinfo.partition(":")
    if not sep or not user or not password or not host:
        raise CalDAVError("格式是 https://账号:密码@服务器[/路径]")
    return Account(unquote(user), unquote(password), f"https://{host.lower()}{parts.path or '/'}", wanted)


def validate(value: str) -> str:
    """Desktop 填写时校验并统一成 https:// 形式（报错不回显内容）。"""
    try:
        account = parse_credential(value)
    except CalDAVError as error:
        raise ValueError(str(error)) from None
    parts = urlsplit(account.url)
    tail = f"#{quote(account.calendar, safe='')}" if account.calendar else ""
    return (f"https://{quote(account.user, safe='@')}:{quote(account.password, safe='')}"
            f"@{parts.netloc}{parts.path}{tail}")


def _account() -> Account:
    from src.agents.mcp_tools import connector_locally_revoked, load_connector_secret
    try:
        secret = load_connector_secret(NAME)
    except FileNotFoundError:
        raise CalDAVError("还没接日历写入：在 Desktop 插件页填「邮箱:App 专用密码」") from None
    except (OSError, ValueError) as error:
        raise CalDAVError(f"日历写入凭据文件不可用：{error}") from None
    if connector_locally_revoked(NAME):
        raise CalDAVError("日历写入凭据已在本机撤销")
    return parse_credential(secret)


def _site(host: str) -> str:
    return ".".join((host or "").lower().rstrip(".").split(".")[-2:])


def _request(account: Account, url: str, method: str, body: bytes, extra: Dict[str, str]) -> bytes:
    from src.agents.web_fetch import FetchError, fetch_bytes
    # 服务器给的地址（例如 iCloud 把日历放在 p01-caldav.icloud.com）只认同一个站点，不把密码带去别处
    if _site(urlsplit(url).hostname or "") != _site(urlsplit(account.url).hostname or ""):
        raise CalDAVError("日历服务器给了别的站点的地址，没有跟过去")
    token = base64.b64encode(f"{account.user}:{account.password}".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {token}", **extra}
    try:
        return fetch_bytes(url, headers=headers, data=body, method=method, max_bytes=_MAX_RESPONSE)
    except FetchError as error:
        hint = {401: "账号或 App 专用密码不对", 403: "没有权限写这个日历", 404: "日历地址不对",
                412: "同一条日程已经存在", 429: "请求太频繁，稍后再试"}.get(error.status or 0)
        raise CalDAVError(f"日历服务器：{hint or error}") from None


def _propfind(account: Account, url: str, depth: str, props: str) -> List[Tuple[str, Any]]:
    body = ('<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" '
            f'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>{props}</d:prop></d:propfind>').encode("utf-8")
    raw = _request(account, url, "PROPFIND", body,
                   {"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise CalDAVError("日历服务器返回的不是 WebDAV 应答") from None
    out = []
    for response in root.findall("d:response", _NS):
        href = (response.findtext("d:href", default="", namespaces=_NS) or "").strip()
        for propstat in response.findall("d:propstat", _NS):
            status = propstat.findtext("d:status", default="", namespaces=_NS) or ""
            prop = propstat.find("d:prop", _NS)
            if " 200 " in f"{status} " and prop is not None and href:
                out.append((urljoin(url, href), prop))
    return out


def _href_of(found: List[Tuple[str, Any]], path: str) -> Optional[str]:
    for base, prop in found:
        href = prop.findtext(path, default="", namespaces=_NS)
        if href and href.strip():
            return urljoin(base, href.strip())
    return None


def discover(account: Account) -> Calendar:
    """找到要写的日历：当前用户 → 日历主目录 → 能放日程的日历（凭据里写了日历名就按名字挑）。"""
    key = f"{account.user}|{account.url}|{account.calendar}"
    cached = _discovered.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    principal = _href_of(_propfind(account, account.url, "0", "<d:current-user-principal/>"),
                         "d:current-user-principal/d:href")
    if not principal:
        raise CalDAVError("日历服务器没告诉我当前用户在哪")
    home = _href_of(_propfind(account, principal, "0", "<c:calendar-home-set/>"), "c:calendar-home-set/d:href")
    if not home:
        raise CalDAVError("日历服务器没告诉我日历放在哪")
    calendars = []
    for url, prop in _propfind(account, home, "1", "<d:displayname/><d:resourcetype/>"
                                                   "<c:supported-calendar-component-set/>"):
        if prop.find("d:resourcetype/c:calendar", _NS) is None:
            continue
        comps = prop.findall("c:supported-calendar-component-set/c:comp", _NS)
        if comps and not any(str(comp.get("name", "")).upper() == "VEVENT" for comp in comps):
            continue        # 只放提醒事项（VTODO）的列表
        name = (prop.findtext("d:displayname", default="", namespaces=_NS) or "").strip()
        calendars.append(Calendar(url if url.endswith("/") else url + "/", name or "（未命名日历）"))
    if not calendars:
        raise CalDAVError("没找到能放日程的日历")
    chosen = calendars[0]
    if account.calendar:
        named = [cal for cal in calendars if cal.name == account.calendar]
        if not named:
            raise CalDAVError(f"没找到名叫「{account.calendar}」的日历，现有：{'、'.join(c.name for c in calendars)[:200]}")
        chosen = named[0]
    _discovered[key] = (time.monotonic(), chosen)
    return chosen


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _when(text: str, local: Any) -> Tuple[Any, bool]:
    raw = re.sub(r"\s+", " ", str(text or "").strip().replace("T", " "))
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).replace(tzinfo=local), False
        except ValueError:
            pass
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), True
    except ValueError:
        raise CalDAVError("时间要写成 YYYY-MM-DD HH:MM（全天日程只写日期）") from None


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """iCalendar 每行不超过 75 字节，长了折行（续行以空格开头），不拆开多字节字符。"""
    out, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > (75 if not out else 74):
            out.append(current)
            current = ""
        current += char
    out.append(current)
    return "\r\n ".join(out)


def build_event(title: str, start: str, end: str = "", location: str = "", notes: str = "",
                now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _local_now()
    local = now.tzinfo or timezone.utc
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:200]
    location = re.sub(r"\s+", " ", str(location or "")).strip()[:200]
    notes = str(notes or "").strip()[:2000]
    if not title:
        raise CalDAVError("日程要有标题")
    begin, all_day = _when(start, local)
    if end:
        finish, end_all_day = _when(end, local)
        if end_all_day != all_day:
            raise CalDAVError("开始和结束要么都带时间，要么都是全天")
        if all_day:
            finish = finish + timedelta(days=1)          # 用户说的结束日包含在内，iCalendar 的 DTEND 不含
    else:
        finish = begin + (timedelta(days=1) if all_day else timedelta(hours=1))
    if finish <= begin:
        raise CalDAVError("结束要晚于开始")
    if finish - begin > timedelta(days=14):
        raise CalDAVError("一条日程最长两周")
    last_moment = datetime.combine(finish, datetime.min.time(), local) if all_day else finish
    if last_moment < now:
        raise CalDAVError("这个时间已经过去了")
    uid = f"{uuid.uuid4()}@vortocode"
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if all_day:
        times = [f"DTSTART;VALUE=DATE:{begin:%Y%m%d}", f"DTEND;VALUE=DATE:{finish:%Y%m%d}"]
    else:
        times = [f"DTSTART:{begin.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
                 f"DTEND:{finish.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//VortoCode//Assistant//ZH", "CALSCALE:GREGORIAN",
             "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", *times, f"SUMMARY:{_escape(title)}"]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if notes:
        lines.append(f"DESCRIPTION:{_escape(notes)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return {"uid": uid, "title": title, "location": location, "notes": notes, "all_day": all_day,
            "start": begin, "end": finish, "ics": "\r\n".join(_fold(line) for line in lines) + "\r\n"}


def _span(event: Dict[str, Any]) -> str:
    begin, finish = event["start"], event["end"]
    if event["all_day"]:
        last = finish - timedelta(days=1)
        first = f"{begin:%m-%d}（周{_WEEKDAY_CN[begin.weekday()]}）"
        return f"{first} 全天" if last == begin else f"{first} 到 {last:%m-%d}（周{_WEEKDAY_CN[last.weekday()]}）全天"
    head = f"{begin:%Y-%m-%d}（周{_WEEKDAY_CN[begin.weekday()]}）{begin:%H:%M}"
    return f"{head}–{finish:%H:%M}" if finish.date() == begin.date() else f"{head} 到 {finish:%m-%d %H:%M}"


def event_preview(event: Dict[str, Any]) -> str:
    extra = (f"\n地点：{event['location']}" if event["location"] else "") + \
            (f"\n备注：{event['notes'][:300]}" if event["notes"] else "")
    return f"在你的日历里新建日程？\n{_span(event)}\n{event['title']}{extra}"


def save_event(event: Dict[str, Any]) -> str:
    """把确认过的日程放进日历，返回日历名字。只新建：同一个 UID 已存在时服务器拒绝，不覆盖。"""
    account = _account()
    calendar = discover(account)
    _request(account, calendar.url + quote(event["uid"], safe="@") + ".ics", "PUT", event["ics"].encode("utf-8"),
             {"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"})
    return calendar.name


async def check() -> str:
    calendar = await asyncio.to_thread(lambda: discover(_account()))
    return f"能写入日历「{calendar.name}」（只会在你确认后新建日程）"


def build_tools() -> list:
    return []


def build_write_tools(confirm: Any) -> list:
    from src.agents.gate import request
    from src.agents.tool import Tool
    from src.agents.trust import INTERACT

    async def _add(args: dict) -> str:
        try:
            event = build_event(str(args.get("title") or ""), str(args.get("start") or ""),
                                str(args.get("end") or ""), str(args.get("location") or ""),
                                str(args.get("notes") or ""))
        except CalDAVError as error:
            return str(error)
        if not await request(confirm, event_preview(event), INTERACT):
            return "用户没有同意，日程没建。"
        try:
            name = await asyncio.to_thread(save_event, event)
        except CalDAVError as error:
            return str(error)
        return f"已在日历「{name}」里建好：{_span(event)} {event['title']}。"

    return [Tool("calendar_add", "在用户的日历里新建一条日程（只新建，不改不删）。每次都会请用户确认。"
                 "时间按用户所在时区写 YYYY-MM-DD HH:MM；全天日程只写日期。不确定日期或时间时先问用户。",
                 {"title": "标题", "start": "开始，YYYY-MM-DD HH:MM 或 YYYY-MM-DD（全天）",
                  "end": "结束，可空（默认一小时；全天默认当天）", "location": "地点，可空", "notes": "备注，可空"},
                 _add, read_only=False, untrusted_source=True, serial=True)]
