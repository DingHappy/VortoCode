"""Process-resident mobile OAuth sessions, sharing original ownership/read leases.

No provider token reaches JS, files or URLs. Restart requires account login
again; native secure storage is a separate future client capability.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlencode
import uuid

from src.gateway.account_access import AccountIdentity, account_origin
from src.gateway.account_registration import AccountOwnerMismatch, AccountRegistration
from src.gateway.account_service import (
    AccountRejected,
    AccountUnavailable,
    configured_account_origin,
    verify_account_access,
)
from src.gateway.account_oauth_http import OAuthTokens, logout_provider, token_request
from src.gateway.mobile_devices import MobileStoreError

CLIENT_ID = "vortocode-mobile-web"
PENDING_TTL = 300
SESSION_TTL = 8 * 3600


class BrowserDenied(RuntimeError):
    pass


class BrowserNotReady(RuntimeError):
    pass


class BrowserCapacity(AccountUnavailable):
    pass


@dataclass(frozen=True)
class BrowserConfig:
    root: str
    origin: str
    issuer: str
    instance_id: str
    revision: int
    owner: AccountIdentity
    name: str

    @property
    def callback(self):
        return self.origin + "/api/mobile/account/callback"

    @property
    def login_cookie(self):
        return "vorto_mobile_login_" + self.instance_id

    @property
    def session_cookie(self):
        return "vorto_mobile_session_" + self.instance_id

    @property
    def context_cookie(self):
        return "vorto_mobile_context_" + self.instance_id


def browser_config(root: str) -> BrowserConfig:
    from src.env_compat import env_compat

    raw = os.getenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN", "").strip()
    if not raw:
        raise BrowserNotReady("此助手尚未启用手机账号登录")
    if not env_compat("VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "").strip():
        raise BrowserNotReady("请先为助手服务配置管理员身份")
    try:
        origin, issuer = account_origin(raw), configured_account_origin()
        data = AccountRegistration(root).read()
        registration = data["registration"]
        if not registration:
            raise BrowserNotReady("请先在电脑端关联此助手的账号")
        owner = AccountIdentity(**registration["owner"])
        if owner.issuer != issuer:
            raise BrowserNotReady("助手账号服务已变化，请在电脑端重新关联")
        return BrowserConfig(
            root, origin, issuer, data["instance_id"], data["revision"], owner, registration["name"]
        )
    except (ValueError, MobileStoreError, AccountUnavailable):
        raise BrowserNotReady("手机账号登录配置暂不可用") from None


@dataclass
class Pending:
    config: BrowserConfig
    proof_hash: str
    verifier: str = field(repr=False)
    expires: float
    context_key: str
    generation: int
    native_challenge: str | None = None
    native_state: str | None = None


@dataclass
class BrowserContext:
    root: str
    expires: float
    generation: int = 0


@dataclass
class BrowserSession:
    config: BrowserConfig
    tokens: OAuthTokens = field(repr=False)
    access_until: float
    expires: float
    device_id: int
    context_key: str
    generation: int
    resource_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    resource_token: str = field(
        default_factory=lambda: "vcm_" + secrets.token_urlsafe(32), repr=False
    )
    lease_until: int = 0
    active: bool = True
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    native: bool = False


@dataclass
class NativeTicket:
    session: BrowserSession
    secret: str = field(repr=False)
    challenge: str
    state: str
    expires: float


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MobileBrowserSessions:
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self.pending: dict[str, Pending] = {}
        self.sessions: dict[str, BrowserSession] = {}
        self.contexts: dict[str, BrowserContext] = {}
        self.tickets: dict[str, NativeTicket] = {}

    def native_ticket(self, pending: Pending, secret: str) -> str:
        session = self.sessions[_digest(secret)]
        self._check(session)
        if not pending.native_challenge or not pending.native_state:
            raise BrowserDenied()
        self.tickets = {
            key: value for key, value in self.tickets.items() if value.expires > self.clock()
        }
        if len(self.tickets) >= 128:
            self.drop(session)
            raise BrowserCapacity()
        session.native = True
        code = secrets.token_urlsafe(32)
        self.tickets[_digest(code)] = NativeTicket(
            session, secret, pending.native_challenge, pending.native_state, self.clock() + 60
        )
        return code

    def redeem_native(self, code: str, verifier: str, state: str) -> dict:
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code) or not re.fullmatch(
            r"[A-Za-z0-9_-]{43,128}", verifier
        ):
            raise BrowserDenied()
        ticket = self.tickets.get(_digest(code))
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if (
            not ticket
            or ticket.expires <= self.clock()
            or not hmac.compare_digest(ticket.challenge, challenge)
            or not hmac.compare_digest(ticket.state, state)
        ):
            raise BrowserDenied()
        self._check(ticket.session)
        self.tickets.pop(_digest(code))
        return {
            "schema_version": 1,
            "token": "vcn_" + ticket.secret,
            "instance_id": ticket.session.config.instance_id,
            "name": ticket.session.config.name,
            "expires_at": int(ticket.session.expires),
        }

    def find_native(self, token: str) -> BrowserSession:
        import re

        if not re.fullmatch(r"vcn_[A-Za-z0-9_-]{43}", token):
            raise BrowserDenied()
        session = self.sessions.get(_digest(token[4:]))
        if not session or not session.native:
            raise BrowserDenied()
        self._check(session)
        return session

    def context(self, config: BrowserConfig, secret: str = "") -> str:
        now = self.clock()
        self.contexts = {key: value for key, value in self.contexts.items() if value.expires > now}
        current = self.contexts.get(_digest(secret)) if secret and len(secret) <= 128 else None
        if current and current.root == config.root:
            return secret
        if len(self.contexts) >= 256:
            protected = {value.context_key for value in self.sessions.values() if value.active}
            protected.update(value.context_key for value in self.pending.values())
            key = next((key for key in self.contexts if key not in protected), None)
            if key is None:
                raise BrowserCapacity("手机连接请求繁忙")
            self.contexts.pop(key)
        secret = secrets.token_urlsafe(32)
        self.contexts[_digest(secret)] = BrowserContext(config.root, now + SESSION_TTL)
        return secret

    def invalidate_context(self, root: str, secret: str) -> None:
        current = self.contexts.get(_digest(secret)) if secret and len(secret) <= 128 else None
        if current and current.root == root:
            current.generation += 1

    def _pending_current(self, pending: Pending) -> bool:
        context = self.contexts.get(pending.context_key)
        return bool(
            context
            and context.expires > self.clock()
            and context.root == pending.config.root
            and context.generation == pending.generation
            and self._current(pending.config)
        )

    def _current(self, config: BrowserConfig) -> bool:
        try:
            return browser_config(config.root) == config
        except BrowserNotReady:
            return False

    def _check(self, session: BrowserSession) -> None:
        context = self.contexts.get(session.context_key)
        if (
            not session.active
            or self.clock() >= session.expires
            or not self._current(session.config)
            or not context
            or context.expires <= self.clock()
            or context.generation != session.generation
        ):
            self.drop(session)
            raise BrowserDenied("登录或助手归属已变化")

    def drop(self, session: BrowserSession) -> None:
        session.active = False
        self.tickets = {
            key: value for key, value in self.tickets.items() if value.session is not session
        }
        for key, value in list(self.sessions.items()):
            if value is session:
                self.sessions.pop(key, None)
        if session.lease_until:
            try:
                AccountRegistration(session.config.root).revoke(session.resource_id)
            except (OSError, KeyError, ValueError, MobileStoreError):
                pass  # BFF session is unusable; any remaining read lease expires within five minutes.

    def begin(
        self, config: BrowserConfig, context_secret: str, previous_proof: str = ""
    ) -> tuple[str, str]:
        context_key = _digest(context_secret)
        context = self.contexts.get(context_key)
        if not context or context.root != config.root or context.expires <= self.clock():
            raise BrowserDenied("请刷新手机入口后登录")
        context.generation += 1
        now = self.clock()
        self.pending = {
            key: value
            for key, value in self.pending.items()
            if value.expires > now
            and not (
                value.config.root == config.root
                and previous_proof
                and hmac.compare_digest(value.proof_hash, _digest(previous_proof))
            )
        }
        if len(self.pending) >= 64:
            raise BrowserCapacity("登录请求繁忙，请稍后重试")
        state, proof, verifier = (
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(32),
        )
        self.pending[_digest(state)] = Pending(
            config, _digest(proof), verifier, now + PENDING_TTL, context_key, context.generation
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        url = (
            config.issuer
            + "/authorize?"
            + urlencode(
                {
                    "client_id": CLIENT_ID,
                    "redirect_uri": config.callback,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                    "device_name": "手机浏览器",
                }
            )
        )
        return url, proof

    def claim(self, config: BrowserConfig, state: str, proof: str) -> Pending:
        if not state or len(state) > 128 or not proof or len(proof) > 128:
            raise BrowserDenied("登录回调无效")
        pending = self.pending.get(_digest(state))
        if (
            not pending
            or pending.config != config
            or self.clock() >= pending.expires
            or not self._pending_current(pending)
            or not hmac.compare_digest(pending.proof_hash, _digest(proof))
        ):
            raise BrowserDenied("登录回调无效")
        self.pending.pop(_digest(state), None)
        return pending

    async def complete(self, pending: Pending, code: str) -> str:
        config = pending.config
        if not self._pending_current(pending) or not code or len(code) > 512:
            raise BrowserDenied("登录回调已失效")
        tokens = await token_request(
            config.issuer,
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": pending.verifier,
                "redirect_uri": config.callback,
                "platform": "mobile-web",
                "app_version": "0.1.0",
            },
        )
        try:
            identity = await verify_account_access(tokens.access, config.issuer)
            if identity.identity != config.owner:
                raise BrowserDenied("请使用与此助手相同的账号登录")
            if not self._pending_current(pending):
                raise BrowserDenied("助手归属已变化")
            for session in list(self.sessions.values()):
                if self.clock() >= session.expires:
                    self.drop(session)
            if len(self.sessions) >= 128:
                raise BrowserCapacity("手机会话已达上限")
            secret = secrets.token_urlsafe(32)
            self.sessions[_digest(secret)] = BrowserSession(
                config,
                tokens,
                self.clock() + tokens.expires_in,
                self.clock() + SESSION_TTL,
                identity.device_id,
                pending.context_key,
                pending.generation,
            )
            return secret
        except BaseException:
            await logout_provider(config.issuer, tokens.access)
            raise

    def find(self, root: str, cookies: dict) -> BrowserSession | None:
        for name, value in cookies.items():
            if name.startswith("vorto_mobile_session_") and len(value) <= 128:
                session = self.sessions.get(_digest(value))
                if (
                    session
                    and session.config.root == root
                    and name == session.config.session_cookie
                ):
                    return session
        return None

    async def grant(self, session: BrowserSession) -> dict:
        async with session.lock:
            self._check(session)
            config = session.config
            if self.clock() + 60 >= session.access_until:
                tokens = None
                try:
                    # Never retry a refresh whose response was lost: rotation may already have committed.
                    tokens = await token_request(
                        config.issuer,
                        {
                            "grant_type": "refresh_token",
                            "client_id": CLIENT_ID,
                            "refresh_token": session.tokens.refresh,
                        },
                    )
                    self._check(session)
                    if tokens.refresh == session.tokens.refresh:
                        raise AccountUnavailable("登录续期响应无效")
                    session.tokens = tokens
                    session.access_until = self.clock() + tokens.expires_in
                except BaseException:
                    self.drop(session)
                    if tokens:
                        await logout_provider(config.issuer, tokens.access)
                    raise
            try:
                identity = await verify_account_access(session.tokens.access, config.issuer)
                self._check(session)
                if identity.identity != config.owner or identity.device_id != session.device_id:
                    raise BrowserDenied("账号会话已变化")
                store = AccountRegistration(config.root, clock=self.clock)
                if self.clock() + 30 >= session.lease_until:
                    grant = await asyncio.to_thread(
                        store.read_lease,
                        identity,
                        "手机浏览器",
                        config.revision,
                        session.resource_id,
                        session.resource_token,
                        session.lease_until,
                    )
                    session.lease_until = grant["device"]["expires_at"]
                self._check(session)
                grant = await asyncio.to_thread(store.authenticate, session.resource_token)
                self._check(session)
                if not grant:
                    raise BrowserDenied("设备读取授权已失效")
                return grant
            except (AccountRejected, AccountOwnerMismatch, BrowserDenied):
                self.drop(session)
                raise BrowserDenied("登录或设备授权已失效") from None

    async def logout(self, session: BrowserSession, *, invalidate=True) -> None:
        context = self.contexts.get(session.context_key)
        if invalidate and context:
            context.generation += 1
        self.drop(session)
        async with session.lock:
            await logout_provider(session.config.issuer, session.tokens.access)
