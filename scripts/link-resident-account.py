#!/usr/bin/env python3
"""One-time owner enrollment through user-approved OAuth and the SSH admin bridge.

No Keychain reads, copied credentials, email matching, or tokens on disk.
Run locally, open the printed loopback URL, then approve the account-service form.
The temporary provider device is revoked after enrollment. Ordinary phones use
the public mobile login, never this administrator setup bridge.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import urllib.error
import urllib.request

ISSUER = "https://account.vortotech.com"
CLIENT_ID = "vortocode-desktop"
PUBLIC_ORIGIN = "https://vortocode.dinghappy.com:18443"

# This program is sent as code; the provider token travels only through stdin.
REMOTE_BRIDGE = r'''
import json,os,sys,urllib.request
from pathlib import Path
try:
 payload=json.loads(sys.stdin.read(4096))
 private={}
 for line in (Path.home()/'.config/vortocode-mobile/environment').read_text().splitlines():
  if line and not line.startswith('#'):
   key,value=line.split('=',1);private[key]=value.strip('"')
 assert private['VORTOCODE_MOBILE_PUBLIC_ORIGIN']==payload['public_origin']
 assert private['VORTOCODE_ACCOUNT_URL']==payload['issuer']
 client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 url='http://127.0.0.1:18081/api/mobile/account/registration'
 headers={'Authorization':'Bearer '+private['VORTOCODE_API_TOKEN'],'Content-Type':'application/json'}
 with client.open(urllib.request.Request(url,headers=headers),timeout=10) as r:
  current=json.loads(r.read(8192))
 assert current['registration'] is None
 body={'issuer':payload['issuer'],'access_token':payload['access_token'],'name':payload['name'],
       'revision':current['revision'],'instance_id':current['instance_id']}
 with client.open(urllib.request.Request(url,data=json.dumps(body).encode(),headers=headers,method='PUT'),timeout=15) as r:
  saved=json.loads(r.read(8192))
 assert saved['registration'] and saved['revision']>current['revision']
 print(json.dumps({'ok':True,'owner_registered':True,'revision':saved['revision']}))
except Exception:
 print(json.dumps({'ok':False,'detail':'Owner enrollment failed or already exists; inspect privately.'}))
 sys.exit(1)
'''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def provider(path, *, form=None, access=None):
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {"Accept": "application/json"}
    if access:
        headers["Authorization"] = "Bearer " + access
    data = urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(ISSUER + path, data=data, headers=headers)
    with client.open(request, timeout=15) as response:
        raw = response.read(16385)
        if response.status != 200 or len(raw) > 16384:
            raise ValueError("Invalid account response")
        return json.loads(raw)


def enroll(host, access, name):
    import shlex

    payload = json.dumps({"issuer": ISSUER, "public_origin": PUBLIC_ORIGIN,
                          "access_token": access, "name": name})
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
         "python3 -c " + shlex.quote(REMOTE_BRIDGE)],
        input=payload, text=True, capture_output=True, timeout=35,
    )
    if result.returncode or len(result.stdout) > 4096:
        raise ValueError("Private owner enrollment failed")
    receipt = json.loads(result.stdout)
    if receipt.get("ok") is not True or receipt.get("owner_registered") is not True:
        raise ValueError("Private owner enrollment failed")
    return receipt


class Enrollment:
    def __init__(self, host, name):
        self.host, self.name = host, name
        self.csrf = secrets.token_urlsafe(32)
        self.state = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(48)
        self.started = False
        self.claimed = False
        self.finished = False
        self.receipt = None
        self.error = None
        self.deadline = time.monotonic() + 600

    def outcome(self):
        """Only non-sensitive setup evidence may leave the helper process."""
        error = self.error
        if not self.finished:
            error = "账号授权窗口已过期，请重新启动关联工具。"
        return {
            **(self.receipt or {}),
            "ok": bool(self.receipt) and not error,
            "status": "completed" if self.finished and not error else (
                "failed" if self.finished else "expired"
            ),
            "error": error,
            "credentials_persisted": False,
            "desktop_keychain_read": False,
        }

    def begin(self, csrf, redirect):
        if self.started or time.monotonic() > self.deadline or not hmac.compare_digest(csrf.encode(), self.csrf.encode()):
            raise ValueError("Setup already started or invalid")
        self.started = True
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")
        return ISSUER + "/authorize?" + urlencode({
            "client_id": CLIENT_ID, "redirect_uri": redirect, "state": self.state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "device_name": self.name + "（一次性账号关联）",
        })

    def complete(self, query, redirect):
        state, code = query.get("state", []), query.get("code", [])
        if (not self.started or self.claimed or self.finished or time.monotonic() > self.deadline
                or len(state) != 1 or not hmac.compare_digest(state[0].encode(), self.state.encode())):
            raise ValueError("Invalid callback")
        self.claimed = True
        access = None
        try:
            if query.get("error") or len(code) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", code[0]):
                raise ValueError("Authorization denied")
            tokens = provider("/oauth/token", form={
                "grant_type": "authorization_code", "client_id": CLIENT_ID,
                "code": code[0], "code_verifier": self.verifier, "redirect_uri": redirect,
                "platform": "resident-owner-setup", "app_version": "1",
            })
            access = tokens.get("access_token")
            if not isinstance(access, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", access):
                raise ValueError("Invalid credential")
            identity = provider("/api/me", access=access)
            if type(identity.get("user_id")) is not int or identity["user_id"] < 1:
                raise ValueError("Stable account identity unavailable")
            self.receipt = enroll(self.host, access, self.name)
        except Exception:
            self.error = "账号关联未完成，请检查账号授权或远端配置。"
        finally:
            if access:
                try:
                    provider("/api/logout", access=access, form={})
                    # Prove cleanup without exposing provider data.
                    try:
                        provider("/api/me", access=access)
                    except urllib.error.HTTPError as error:
                        if error.code != 401:
                            raise ValueError("Cleanup not confirmed") from None
                    else:
                        raise ValueError("Temporary device remained active")
                    if self.receipt:
                        self.receipt["temporary_device_revoked"] = True
                except Exception:
                    self.error = "关联步骤已执行，但临时设备退出尚未确认，请在账号设备页检查。"
            self.verifier = ""
            self.finished = True


def make_handler(setup):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Includes callback codes: never log request paths.

        def handle_one_request(self):
            self.connection.settimeout(10)
            return super().handle_one_request()

        @property
        def origin(self):
            return "http://127.0.0.1:" + str(self.server.server_port)

        def reply(self, status, body="", *, location=None):
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            # Chromium sends Origin: null for a form navigation under no-referrer.
            # Keep the initial same-origin form verifiable; cross-origin OAuth
            # navigation still receives no referrer under same-origin policy.
            self.send_header("Referrer-Policy", "same-origin" if self.path == "/" else "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self' " + ISSUER)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if location:
                self.send_header("Location", location)
            encoded = body.encode()
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def trusted_host(self):
            return self.headers.get("Host") == urlsplit(self.origin).netloc

        def do_GET(self):
            if not self.trusted_host() or len(self.path) > 4096:
                return self.reply(400)
            parsed = urlsplit(self.path)
            if parsed.path == "/" and not parsed.query and not setup.started:
                return self.reply(200, '<!doctype html><meta charset="utf-8"><title>关联 97 的 VortoCode 助手</title>'
                    '<h1>关联 97 的常驻助手</h1><p>请登录你希望与手机共用的 VortoCode 账号。账号关联后，手机可读取原助手资料、主聊天和目标。</p>'
                    '<p>不会读取桌面端钥匙串；一次性关联设备会在完成后退出。</p>'
                    '<form method="post" action="/start"><input type="hidden" name="csrf" value="'
                    + html.escape(setup.csrf, quote=True) + '"><button>前往 VortoCode 账号授权</button></form>')
            if parsed.path == "/callback":
                if time.monotonic() > setup.deadline and not setup.finished:
                    setup.error = "账号授权窗口已过期，请重新启动关联工具。"
                    setup.finished = True
                    setup.verifier = ""
                    return self.reply(303, location="/done")
                try:
                    setup.complete(parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8), self.origin + "/callback")
                except ValueError:
                    return self.reply(400, "无效或已使用的回调。")
                return self.reply(303, location="/done")
            if parsed.path == "/done" and not parsed.query and setup.finished:
                message = setup.error or "97 的常驻助手已关联。现在可用同一账号登录手机入口。"
                return self.reply(200, '<!doctype html><meta charset="utf-8"><title>账号关联结果</title><h1>'
                    + html.escape(message) + '</h1><p>可以关闭本页。</p>')
            return self.reply(404)

        def do_POST(self):
            if (not self.trusted_host() or self.path != "/start"
                    or self.headers.get("Origin") != self.origin
                    or self.headers.get("Content-Type") != "application/x-www-form-urlencoded"):
                return self.reply(403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError
                body = parse_qs(self.rfile.read(length).decode(), max_num_fields=2)
                if set(body) != {"csrf"} or len(body["csrf"]) != 1:
                    raise ValueError
                destination = setup.begin(body["csrf"][0], self.origin + "/callback")
            except (ValueError, UnicodeError):
                return self.reply(400)
            return self.reply(303, location=destination)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-host", default="vorto-code")
    parser.add_argument("--name", default="97 常驻助手")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,100}", args.ssh_host) or not 1 <= len(args.name) <= 50:
        parser.error("Invalid SSH host or name")
    setup = Enrollment(args.ssh_host, args.name)
    with HTTPServer(("127.0.0.1", 0), make_handler(setup)) as server:
        server.timeout = .5
        print(json.dumps({"setup_url": "http://127.0.0.1:" + str(server.server_port), "expires_in": 600}), flush=True)
        while time.monotonic() < setup.deadline:
            server.handle_request()
            if setup.finished:
                break
        if setup.finished:
            # Keep result delivery independent of OAuth expiry; never extend
            # the original authorization deadline to serve the clean page.
            result_deadline = time.monotonic() + 20
            while time.monotonic() < result_deadline:
                server.handle_request()
    receipt = setup.outcome()
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt), flush=True)
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
