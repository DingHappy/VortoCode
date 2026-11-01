"""Human-approved website connections using the existing browser and gate."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

from src.agents.gate import request
from src.agents.confirmation_context import confirmation_turn, presenting_website
from src.agents.tool import Tool, ToolResource
from src.agents.trust import INTERACT
from src.browser.backend import BrowserBackend, BrowserLaunch, chromium_backend
from src.browser.action_state import BrowserActionState
from src.browser.connections import BrowserConnections, safe_url, site_origin
from src.browser.page_privacy import AUTH_STATE_JS, ELEMENT_JS, SNAPSHOT_JS
from src.gateway.process_lease import ProcessLease

_LIVE = set()
_MAX_OPEN = 8
_WAIT = "浏览器已交还真人：请在专用窗口处理登录/验证码或核对账号，需由主人确认恢复。不会自动轮询、截图或重复提交。"
_UNCERTAIN = "上次网站操作结果不明，已阻止后续导航、点击、输入和滚动。请先读取当前页面或真人核对；用 browser_login 明确确认恢复，不会重放原操作。"


class PendingBrowserAction(ValueError):
    pass


def _display_origin(url):
    try:
        return site_origin(url)
    except ValueError:
        return safe_url(url).rstrip("/").removesuffix(":80")


class BrowserSession:
    def __init__(self, registry: BrowserConnections, backend: BrowserBackend):
        self.registry, self.backend = registry, backend
        self.browser = None
        self.connection = None
        self.human = False
        self.confirmed = False
        self.lock = asyncio.Lock()
        self.lifecycle = asyncio.Lock()
        self.lease = None
        self.temporary = None
        self.closed = False
        self.cleaning = False
        self.display_origin = ""
        self.login_requested_turn = None
        self.declined_turn = None
        self.declined_requests = set()
        self.actions = BrowserActionState()

    async def open(self, connection=None):
        async with self.lifecycle:
            if self.closed:
                raise ValueError("浏览器正在关闭")
            if self.cleaning:
                raise ValueError("浏览器清理未确认，请先重试关闭")
            if self.actions.path is None and self.actions.read() and connection:
                raise PendingBrowserAction(_UNCERTAIN)
            mode = connection["device_mode"] if connection else "desktop"
            if not self.backend.supports(mode):
                raise ValueError("当前浏览器驱动不支持此网页模式")
            if self.browser is not None and getattr(self.browser, "running", False):
                if self.connection != connection:
                    raise ValueError("请先 browser_close 关闭当前连接，再切换网站或账号")
                return self.browser
            if self.browser is not None:
                await self._close()
            if len(_LIVE) >= _MAX_OPEN and self not in _LIVE:
                raise ValueError("本运行端浏览器已满，请先关闭不再使用的连接")
            _LIVE.add(self)
            try:
                if connection:
                    owner_path = self.registry.profile_path(connection["id"])
                    path = self.registry.profile_path(connection["id"], backend_id=self.backend.backend_id)
                else:
                    # Never silently load the old shared browser-profile cookies.
                    self.temporary = tempfile.TemporaryDirectory(prefix="vortocode-anonymous-")
                    path = Path(self.temporary.name)
                    owner_path = path
                try:
                    self.lease = ProcessLease(owner_path / ".vortocode-owner.lock").acquire()
                except RuntimeError:
                    raise ValueError("网站登录会话正被另一个执行者占用，请先完成或关闭它") from None
                if connection:
                    self.actions = BrowserActionState(owner_path)
                elif self.actions.path is not None:
                    self.actions = BrowserActionState()
                self.browser = self.backend.create(BrowserLaunch(path, mode))
                self.connection = connection
                self.human, self.confirmed = bool(connection), False
                await self.browser.start()
                if self.closed:
                    raise ValueError("运行端正在关闭，未打开网站")
                return self.browser
            except BaseException:
                await self._close()
                raise

    async def _close(self):
        self.cleaning = True
        if self.browser is not None:
            await self.browser.close()  # Failed cleanup retains the lease/slot for retry.
        if self.lease:
            self.lease.release()
        if self.temporary:
            self.temporary.cleanup()
        self.browser = self.lease = self.temporary = self.connection = None
        self.human = self.confirmed = False
        # Keep anonymous pending actions bound to the last displayed origin.
        self.cleaning = False
        _LIVE.discard(self)

    async def close(self, *, shutdown=False):
        if shutdown:
            self.closed = True
        async with self.lifecycle:
            await self._close()

    def current_grant(self):
        if self.closed or self.cleaning:
            return False
        return self.connection is None or self.registry.get(self.connection["id"]) == self.connection


async def shutdown_browsers():
    results = await asyncio.gather(*(item.close(shutdown=True) for item in tuple(_LIVE)), return_exceptions=True)
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("浏览器清理未确认，未释放其占位")


def build_tools(repo_root, confirm, *, browser_factory=None, connections=None, backend=None):
    from src.agents.tools.browser import restricted_reason
    registry = connections or BrowserConnections(repo_root)

    if browser_factory is not None and backend is not None:
        raise ValueError("Use one trusted browser backend factory")
    selected = backend if backend is not None else chromium_backend()
    if not isinstance(selected, BrowserBackend):
        raise ValueError("Browser backend must be supplied by trusted host assembly")
    if browser_factory is not None:  # Compatibility for existing trusted tests.
        selected = BrowserBackend(selected.backend_id, selected.api_version, selected.device_modes,
                                  lambda _launch: browser_factory())
    holder = BrowserSession(registry, selected)

    def resource_status():
        row = holder.connection
        pending = pending_action()
        stopped = holder.browser is not None and not getattr(holder.browser, "running", False)
        state = ("cleanup_pending" if holder.cleaning else "unknown" if holder.closed or stopped
                 else "needs_reconciliation" if pending else
                 "needs_human" if holder.human else "active" if holder.browser is not None else "idle")
        return {"state": state, "target": row["origin"] if row else holder.display_origin,
                "account": row["account_label"] if row else "未登记账号的临时会话" if holder.browser else "",
                "approval": "each_action", "detail": "上次操作待人工核对，不会重放；不是业务成功回执。" if pending else
                "仅当前会话记录；动作前复核连接授权，登录与账号仍需真人核对。"}

    resource = ToolResource("browser", "browser", "网站浏览器", resource_status)

    async def ask_website(message, action, *, connection=None, origin=""):
        row = connection or holder.connection
        details = {
            "version": 1, "action": action,
            "origin": row["origin"] if row else origin or holder.display_origin,
            "account_label": row["account_label"] if row else "临时匿名窗口",
            "device_mode": row["device_mode"] if row else "desktop",
        }
        turn = confirmation_turn()
        if turn is not holder.declined_turn:
            holder.declined_turn = turn
            holder.declined_requests.clear()
        key = tuple(details.values())
        if turn is not None and key in holder.declined_requests:
            return False
        with presenting_website(details):
            try:
                allowed = await request(confirm, message, INTERACT)
            except BaseException:
                if turn is not None:
                    holder.declined_requests.add(key)
                raise
        if not allowed and turn is not None:
            holder.declined_requests.add(key)
        return allowed

    async def raw_url():
        return str(await holder.browser.evaluate("location.href") or "")

    def pending_action():
        return holder.actions.read()

    def require_clear():
        if pending_action():
            raise PendingBrowserAction(_UNCERTAIN)

    async def dispatch(action, operation):
        require_clear()
        actions, browser, lease = holder.actions, holder.browser, holder.lease
        if lease is None or not holder.current_grant():
            raise ValueError("网站执行所有权无效，未发出操作")
        marker = actions.begin(action)
        # Cancellation, transport failure and readback failure all retain it.
        result = await operation()
        if (not holder.current_grant() or holder.browser is not browser
                or holder.lease is not lease or holder.actions is not actions):
            raise PendingBrowserAction(_UNCERTAIN)
        actions.complete(marker)
        return result

    async def action_readback():
        if await guard():
            raise PendingBrowserAction(_UNCERTAIN)
        page = await snapshot()
        if page == _WAIT:
            raise PendingBrowserAction(_UNCERTAIN)
        return page

    def grant_error():
        return "网站连接已撤销、变化或不属于此工作区；不会继续使用登录态。" if not holder.current_grant() else ""

    async def guard(*, resume=False, expected_origin=""):
        if holder.browser is None or not getattr(holder.browser, "running", False):
            return "浏览器还没打开；先用 browser_open 或 browser_connect。"
        error = grant_error()
        if error:
            return error
        if holder.human and not resume:
            return _WAIT  # Never inspect or navigate while the human owns login.
        url = await raw_url()
        reason = restricted_reason(url)
        if expected_origin and _display_origin(url) != expected_origin:
            holder.human, holder.confirmed = True, False
            return _WAIT  # Anonymous handoffs must also return to the shown site.
        if holder.connection:
            try:
                same = site_origin(url) == holder.connection["origin"]
            except ValueError:
                same = False
            if not same:
                host = (urlsplit(url).hostname or "").lower()
                if reason and host not in {"accounts.google.com", "appleid.apple.com", "login.live.com", "login.microsoftonline.com"}:
                    await holder.browser.navigate("about:blank")
                    return "已停止：当前页面不能操作。" + reason
                holder.human = True
                holder.confirmed = False
                return _WAIT  # SSO can remain visible to the human, never to the agent.
        if reason:
            await holder.browser.navigate("about:blank")
            return "已停止：当前页面不能操作。" + reason
        if re.search(r"(?:^|/)(?:login|signin|sign-in|oauth|sso|mfa|2fa|two-factor|password|passwd|credentials|secrets|tokens?|api[_-]?keys?|security|billing|checkout|payment)(?:\.(?:php|html?|aspx?))?(?:/|$)",
                     unquote(urlsplit(url).path).lower()):
            holder.human, holder.confirmed = True, False
            return _WAIT
        state = await holder.browser.evaluate(AUTH_STATE_JS)
        if not isinstance(state, dict) or type(state.get("protected")) is not bool:
            return "页面登录状态无法核对，未读取内容或执行操作。"
        if state["protected"]:
            holder.human, holder.confirmed = True, False
            return _WAIT
        return ""

    async def snapshot():
        page = await holder.browser.evaluate(SNAPSHOT_JS)
        if not isinstance(page, dict) or page.get("needs_login"):
            holder.human, holder.confirmed = True, False
            return _WAIT
        return "\n".join([f"标题：{page.get('title') or '（无）'}", f"地址：{safe_url(str(page.get('url') or ''))}",
            "可交互元素：", *list(page.get("items") or []), "页面文字：", str(page.get("text") or "")])

    async def activate(connection):
        if holder.browser is not None and not getattr(holder.browser, "running", False):
            await holder.close()
        if holder.browser is not None and holder.connection != connection:
            return "请先 browser_close 关闭当前连接，再切换网站或账号。"
        await holder.open(connection)
        return ""

    async def open_page(args):
        url = str(args.get("url") or "").strip()
        reason = restricted_reason(url)
        if reason or url == "about:blank":
            return reason or "需要 http(s) 网站地址。"
        connection = holder.connection
        if args.get("connection_id"):
            connection = registry.get(str(args["connection_id"]))
            if connection is None:
                return "网站连接不存在或不属于此工作区。"
        if connection and site_origin(url) != connection["origin"]:
            return "网址不属于此连接的精确站点，未打开。"
        if not selected.supports(connection["device_mode"] if connection else "desktop"):
            return "当前浏览器驱动不支持此网页模式，未打开；不会自动切换驱动。"
        if holder.browser is not None:
            require_clear()
        if holder.browser is not None and holder.human:
            return _WAIT
        if connection:
            if not await ask_website(f"使用网站连接打开页面，并允许读取交给模型处理？\n"
                f"网站：{connection['origin']}\n账号标签：{connection['account_label']}\n模式：{connection['device_mode']}\n"
                "登录态仅留在本机专用浏览器，不代表已核实登录账号。页面可能产生站点副作用；私密 URL 参数不在确认记录中展示。", "open", connection=connection):
                return "主人未同意导航，未打开页面。"
            if registry.get(connection["id"]) != connection:
                return "网站连接已变化，请重新核对。"
        error = await activate(connection)
        if error:
            return error
        if pending_action():
            if connection:
                holder.display_origin = connection["origin"]
            raise PendingBrowserAction(_UNCERTAIN)
        holder.display_origin = _display_origin(url)
        require_clear()  # A previous process may have left a durable marker.
        async def navigate():
            await holder.browser.navigate(url)
            return await guard() or await snapshot()
        return await dispatch("open", navigate)

    async def connect(args):
        url = str(args.get("url") or "")
        reason = restricted_reason(url)
        if reason:
            return reason
        origin = site_origin(url)
        label = registry.label(args.get("account_label"))
        mode = args.get("device_mode", "desktop")
        if mode not in {"desktop", "mobile"}:
            return "模式只能为 desktop 或 mobile（手机网页，不是原生 App）。"
        if not selected.supports(mode):
            return "当前浏览器驱动不支持此网页模式，未登记；不会自动切换驱动。"
        allowed = await ask_website(f"登记网站连接，并允许当前工作区在本机使用专用登录会话？\n"
            f"网站：{origin}\n账号标签：{label}\n模式：{mode}\n"
            "标签不要填密码或验证码。登录后页面可交给模型处理；密码/Cookie 不交给模型。", "connect",
            connection={"origin": origin, "account_label": label, "device_mode": mode})
        if not allowed:
            return "主人未同意，未登记网站连接。"
        row = registry.approve(origin, label, mode)
        return "已登记网站连接；请用 browser_open(url, connection_id) 打开，再 browser_login 交还真人核对登录。\n" + json.dumps(row, ensure_ascii=False)

    async def login(_args):
        if holder.browser is None or not getattr(holder.browser, "running", False):
            return "请先打开专用浏览器。"
        error = grant_error()
        if error:
            return error
        pending = pending_action()
        actions, browser, lease = holder.actions, holder.browser, holder.lease
        turn = confirmation_turn()
        if turn is not None and holder.login_requested_turn is turn:
            if holder.confirmed and not holder.human and not pending:
                return "本轮已完成人工确认，沿用当前会话；不重复请求接管。"
            return _WAIT + " 本轮已请求过人工接管，不再重复询问；需要再次核对时请主人发送新消息。"
        holder.human, holder.confirmed = True, False
        holder.login_requested_turn = turn
        expected_origin = holder.display_origin
        label = holder.connection["account_label"] if holder.connection else "当前临时窗口"
        message = (f"浏览器交还真人，请在专用窗口完成登录、扫码或验证码，核对账号标签：{label}。\n"
            "回到本站页面后才点同意恢复；不同意则继续暂停。不要在对话中发送密码、Cookie 或验证码。")
        if pending:
            message += ("\n上次网站操作结果不明。请先等待原动作结束，人工核对是否已提交及当前账号。"
                        "同意只解除待核对阻断，不会重放、不代表业务成功；仍需重新读取页面。")
        allowed = await ask_website(message, "login")
        if not allowed:
            return _WAIT
        error = await guard(resume=True, expected_origin=expected_origin)
        if error:
            return error
        page = await snapshot()
        if page == _WAIT:
            return page
        if pending:
            error = await guard(resume=True, expected_origin=expected_origin)
            if error:
                return error
            if (not holder.current_grant() or holder.actions is not actions
                    or holder.browser is not browser or holder.lease is not lease or lease is None):
                raise PendingBrowserAction(_UNCERTAIN)
            actions.complete(pending)
        holder.human, holder.confirmed = False, True
        return "主人已确认恢复；这是人工确认，不是站点账号身份的独立验证。\n" + page

    async def get_snapshot(_args):
        return await guard() or await snapshot()

    async def site_status(_args):
        error = await guard()
        if error:
            return error
        from src.browser import tangpt
        before = await raw_url()
        try:
            supported = site_origin(before) == tangpt.ORIGIN
        except ValueError:
            supported = False
        if not supported:
            return json.dumps({"supported": False, "site": safe_url(before), "attendance": "unknown",
                               "next_step": "当前只支持唐帕的可见页面核对，不猜测其他站点状态。"}, ensure_ascii=False)
        browser, lease = holder.browser, holder.lease
        raw = await browser.evaluate(tangpt.STATUS_JS)
        error = await guard()
        if error:
            return error
        if (holder.browser is not browser or holder.lease is not lease or not holder.current_grant()
                or await raw_url() != before):
            raise ValueError("网站状态核对期间页面或授权变化，未采用观察结果")
        if isinstance(raw, dict) and raw.get("protected") is True:
            holder.human, holder.confirmed = True, False
            return _WAIT
        result = tangpt.project_status(raw)
        result.update(supported=True, observed_at=datetime.now(timezone.utc).isoformat(),
                      pending_action=bool(pending_action()))
        if result["pending_action"]:
            result["next_step"] = "上次操作仍待人工核对；本次只读结果不会解除阻断或重放动作。"
        return json.dumps(result, ensure_ascii=False)

    async def element(ref):
        try:
            number = int(ref)
        except (TypeError, ValueError):
            return None
        return await holder.browser.evaluate(ELEMENT_JS % number) if number > 0 else None

    async def interact(args, *, typing=False):
        require_clear()
        error = await guard()
        if error:
            return error
        item = await element(args.get("ref"))
        if not item:
            return "元素不存在，请重新读取页面。"
        if item.get("sensitive") or item.get("type") == "password":
            holder.human, holder.confirmed = True, False
            return "密码或验证码输入需要真人处理。" + _WAIT
        text = str(args.get("text") or "")
        if typing and (not text or not item.get("editable")):
            return "需要可输入元素和非空 text。"
        before = await raw_url()
        label = item.get("label") or item.get("tag") or "元素"
        message = f"在专用浏览器中{'输入' if typing else '点击'}：\n站点：{safe_url(before)}\n元素：{label}"
        if typing:
            message += "\n内容：" + text[:300]
        allowed = await request(confirm, message, INTERACT)
        if not allowed:
            return "主人没有同意这次操作。"
        error = await guard()
        if error:
            return error
        fresh = await element(args.get("ref"))
        if await raw_url() != before or fresh != item:
            return "确认期间页面或元素已变化，未执行；请重新读取。"
        async def perform():
            await holder.browser.click_at(item["x"], item["y"])
            if typing:
                await holder.browser.insert_text(text)
                if str(args.get("submit") or "").lower() in {"1", "true", "yes"}:
                    await holder.browser.press_enter()
            await holder.browser.wait_ready()
            return ("已输入。\n" if typing else "已点击。\n") + await action_readback()
        return await dispatch("type" if typing else "click", perform)

    async def screenshot(_args):
        error = await guard()
        if error:
            return error
        state = await holder.browser.evaluate(AUTH_STATE_JS)
        if not isinstance(state, dict) or state.get("protected") is not False or state.get("frames") is not False:
            return "页面含嵌入内容，未截屏；请真人查看，避免把嵌入登录内容交给模型。"
        data = await holder.browser.screenshot()
        out = Path(repo_root) / ".vortocode" / "artifacts"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"browser-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.png"
        path.write_bytes(data)
        return f"已截图：{path.relative_to(repo_root)}"

    async def scroll(args):
        require_clear()
        error = await guard()
        if error:
            return error
        direction = args.get("direction", "down")
        if direction not in {"up", "down"}:
            return "direction 只能为 up 或 down。"
        amount = int(args.get("amount", 500))
        if not 1 <= amount <= 1200:
            return "amount 需为 1—1200 CSS 像素。"
        before = await raw_url()
        if not await request(confirm, f"在 {safe_url(before)} 向 {direction} 滚动 {amount} 像素？", INTERACT):
            return "主人未同意滚动。"
        error = await guard()
        if error or await raw_url() != before:
            return error or "页面已变化，未滚动。"
        async def perform():
            await holder.browser.scroll(amount if direction == "down" else -amount)
            return await action_readback()
        return await dispatch("scroll", perform)

    async def revoke(args):
        connection_id = str(args.get("connection_id") or "")
        row = registry.get(connection_id)
        if row is None:
            return "网站连接不存在或不属于此工作区。"
        if not await request(confirm, f"撤销助手使用 {row['origin']} / {row['account_label']} 的连接？\n"
            "登录数据仍保留在本机，这不等于退出网站或撤销网站授权。", INTERACT):
            return "主人未同意，连接保持不变。"
        registry.revoke(connection_id)
        if holder.connection == row:
            await holder.close()
        return "网站连接已撤销；本机登录数据仍保留，不再允许助手通过此连接使用。"

    async def close(_args):
        if not await request(confirm, "关闭当前专用浏览器？已登记网站的本机登录目录保留。", INTERACT):
            return "主人未同意关闭。"
        await holder.close()
        return "已关闭本会话的专用浏览器。"

    async def listing(_args):
        return json.dumps({"connections": registry.list(), "active": holder.connection,
            "backend": {"id": selected.backend_id, "api_version": selected.api_version,
                        "device_modes": sorted(selected.device_modes)},
            "state": "cleanup_pending" if holder.cleaning else "needs_reconciliation" if pending_action() else "needs_human" if holder.human else
                "user_confirmed" if holder.confirmed else "unverified"}, ensure_ascii=False)

    def wrapped(handler, name):
        async def run(args):
            async with holder.lock:
                try:
                    result = await handler(args)
                    # A real turn presents one handoff immediately, without a
                    # second model tool call. Never retry after refusal, timeout,
                    # or an early resume on the same sensitive page.
                    if (name != "browser_login" and confirmation_turn() is not None
                            and holder.human and isinstance(result, str) and _WAIT in result):
                        resumed = await login({})
                        return result.replace(_WAIT, "").strip() + "\n" + resumed
                    return result
                except PendingBrowserAction:
                    return {"ok": False, "text": _UNCERTAIN}
                except Exception:
                    return {"ok": False, "text": "浏览器操作未完成或结果无法核实，请人工核对；不会自动重试。"}
        return run

    specs = [
        ("browser_connections", "列出本工作区已授权的网站连接及人工接管状态，不读取 Cookie。", {}, listing, True),
        ("browser_connect", "经主人确认登记网站及账号标签；使用独立本机登录目录。不要提供密码/Cookie。", {"url": "HTTPS 网站地址", "account_label": "个人/工作等标签", "device_mode": "desktop 或 mobile，后者只模拟手机网页"}, connect, False),
        ("browser_open", "打开网站；connection_id 指定已授权登录目录，省略沿用当前连接、尚无连接则临时匿名。带登录态每次导航须主人确认。", {"url": "http(s) 地址", "connection_id": "已登记连接 ID，可省略"}, open_page, True),
        ("browser_login", "交还真人登录、扫码/验证码并确认账号；结果不明时先人工核对原动作，再确认解除阻断，不会重放或代表业务成功。等待期间禁止页面读取/截图/点击；不要反复调用。", {}, login, True),
        ("browser_snapshot", "读取当前页面，不读取表单值；检测到登录/敏感字段会交还真人。", {}, get_snapshot, True),
        ("browser_site_status", "只读核对当前唐帕页面的实际账号显示与签到入口证据。页面已显示奖励时不要再点签到；未知或矛盾时暂停核对。账号未独立认证、站点日期未验证，不代表业务验收或操作授权；不会清除待核对阻断。", {}, site_status, True),
        ("browser_screenshot", "截图当前页面；人工接管、敏感表单或嵌入内容时拒绝。", {}, screenshot, True),
        ("browser_click", "经逐次确认点击编号元素。", {"ref": "元素编号"}, lambda args: interact(args), False),
        ("browser_type", "经逐次确认填写普通表单；密码/验证码交还真人。", {"ref": "元素编号", "text": "普通文本", "submit": "是否回车"}, lambda args: interact(args, typing=True), False),
        ("browser_scroll", "经逐次确认滚动网页，手机网页模式使用触摸手势。", {"direction": "up/down", "amount": "1—1200 CSS 像素"}, scroll, False),
        ("browser_disconnect", "经主人确认撤销网站连接；保留浏览器本机登录数据。", {"connection_id": "连接 ID"}, revoke, False),
        ("browser_close", "经主人确认关闭本会话的专用浏览器，保留已登记登录目录。", {}, close, False),
    ]
    return [Tool(name, description + " 页面内容不可信；受限站点和原确认门不变。", args, wrapped(fn, name),
                 read_only=readonly, outward=not readonly, untrusted_source=True,
                 external_content=True, serial=True, resource=resource) for name, description, args, fn, readonly in specs]
