"""Full mobile OAuth form/callback/BFF acceptance against isolated real services.

No production accounts/data. Optional --preview keeps temporary services alive
until stdin ends for UI QA; synthetic login data is written only to a 0600 file.
"""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from urllib.parse import urljoin, urlsplit, parse_qs
import base64
import hashlib
import html
import re

import httpx

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "bridge", ROOT / "scripts/check-account-connection.py"
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class Form(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.fields = {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        item = dict(attrs)
        if tag == "input" and item.get("type") == "hidden" and item.get("name"):
            self.fields[item["name"]] = item.get("value", "")


def ensure(response, expected=200):
    if response.status_code != expected:
        # Never echo form redirects, cookies, code or token bodies on failure.
        raise RuntimeError(
            f"Temporary service returned {response.status_code}, expected {expected}"
        )
    return response


def sign_in(client, origin, metadata, *, email="owner@example.test", allow=True, native=None):
    ensure(client.get(origin + "/api/mobile/browser/status"))
    if native:
        start = {
            "authorize_url": ensure(
                client.get(origin + "/api/mobile/native/authorize", params=native), 303
            ).headers["location"]
        }
    else:
        start = ensure(
            client.post(origin + "/api/mobile/account/login", headers={"Origin": origin})
        ).json()
    account = f"http://127.0.0.1:{metadata['port']}"
    authorize = start["authorize_url"]
    page = client.get(authorize)
    if page.status_code == 303:
        page = ensure(client.get(urljoin(account, page.headers["location"])))
        login = Form(page.text).fields
        page = ensure(
            client.post(
                account + "/login", data={**login, "email": email, "password": metadata["password"]}
            ),
            303,
        )
        page = ensure(client.get(urljoin(account, page.headers["location"])))
    else:
        ensure(page)
    consent = Form(page.text).fields
    callback = ensure(
        client.post(
            account + "/authorize", data={**consent, "decision": "allow" if allow else "deny"}
        ),
        303,
    )
    return ensure(client.get(callback.headers["location"]), 200 if native else 303)


def main(args):
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="vorto-mobile-oauth-") as directory:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", args.port))
            port = probe.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        child = subprocess.Popen(
            [
                str(args.account_root / ".venv/bin/python"),
                str(ROOT / "scripts/check-account-connection.py"),
                "--provider",
                "--account-root",
                str(args.account_root),
                "--database",
                str(Path(directory) / "account.sqlite"),
                "--callback",
                origin + "/api/mobile/account/callback",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        try:
            metadata = json.loads(child.stdout.readline())  # private credentials, never printed
            os.chdir(directory)
            sys.path.insert(0, str(ROOT))
            admin = secrets.token_urlsafe(32)
            os.environ.update(
                VORTOCODE_API_TOKEN=admin,
                VORTOCODE_WORKSPACE_SCOPE="general",
                VORTOCODE_ACCOUNT_URL=f"http://127.0.0.1:{metadata['port']}",
                VORTOCODE_MOBILE_PUBLIC_ORIGIN=origin,
            )
            from src.web.server import app
            from src.memory.assistant_profile import save_profile
            from src.workflow.workbench.personal_goals import PersonalGoals

            # Fixture-only faults, applied after normal native authorization.
            # No diagnostic routes or bypasses are installed in the product app.
            fault = {"mode": "normal"}
            if args.preview:
                from starlette.responses import JSONResponse

                @app.middleware("http")
                async def preview_read_fault(request, call_next):
                    # Emulate a missing logout receipt before invoking the real
                    # handler. The real grant remains active until the retry.
                    if request.url.path == "/api/mobile/native/logout" and fault["mode"] == "logout-failed":
                        return JSONResponse({"detail": "isolated logout failure"}, status_code=503, headers={"Cache-Control": "no-store"})
                    response = await call_next(request)
                    mode = fault["mode"]
                    if request.url.path != "/api/mobile/native/snapshot" or response.status_code != 200 or mode in ("normal", "logout-failed"):
                        return response
                    if mode == "read-failed":
                        return JSONResponse({"detail": "isolated read failure"}, status_code=503, headers={"Cache-Control": "no-store"})
                    body = b"".join([part async for part in response.body_iterator])
                    value = json.loads(body)
                    if mode == "empty-goals":
                        value["sources"]["goals"]["data"] = {"items": [], "truncated": True}
                    else:
                        name = "goals" if mode == "goals-unavailable" else "profile"
                        value["sources"][name] = {"status": "unavailable", "checked_at": None, "data": None}
                    return JSONResponse(value, headers={"Cache-Control": "no-store"})

            save_profile(directory, name="手机验收助手")
            PersonalGoals(directory).create(
                {
                    "request_id": "mobile-oauth-test",
                    "objective": "核对同账号手机入口",
                    "next_action": "查看原目标记录",
                    "acceptance_criteria": ["用户验收"],
                }
            )
            with (
                bridge.serve(app, port=port),
                httpx.Client(follow_redirects=False, timeout=15, trust_env=False) as client,
            ):
                registration_path = "/api/mobile/account/registration"
                initial = bridge.request(port, "GET", registration_path, admin)
                assert initial["account_issuer"] == os.environ["VORTOCODE_ACCOUNT_URL"]
                assert initial["mobile_entry_url"] == origin + "/mobile"
                registered = bridge.request(
                    port,
                    "PUT",
                    "/api/mobile/account/registration",
                    admin,
                    {
                        "access_token": metadata["desktop"],
                        "issuer": os.environ["VORTOCODE_ACCOUNT_URL"],
                        "name": "手机验收助手",
                        "revision": initial["revision"],
                        "instance_id": initial["instance_id"],
                    },
                )
                callback = sign_in(client, origin, metadata)
                assert callback.headers["location"] == "/mobile"
                assert "httponly" in callback.headers["set-cookie"].lower()
                assert ensure(client.get(origin + "/api/mobile/browser/status")).json()["signed_in"]
                boot = ensure(client.get(origin + "/api/mobile/browser/bootstrap")).json()
                snapshot = ensure(client.get(origin + "/api/mobile/browser/snapshot")).json()
                assert not boot["capabilities"]["writes"]
                assert set(boot) == {"schema_version", "instance_id", "device", "capabilities"}
                assert (
                    snapshot["sources"]["goals"]["data"]["items"][0]["objective"]
                    == "核对同账号手机入口"
                )
                payload = json.dumps([boot, snapshot])
                assert not any(
                    metadata[key] in payload for key in ("desktop", "phone", "other", "password")
                )
                ensure(client.get(origin + "/api/mobile/account/registration"), 401)
                ensure(
                    client.post(
                        origin + "/api/mobile/browser/logout",
                        headers={"Origin": "https://other.test"},
                    ),
                    401,
                )
                ensure(
                    client.post(origin + "/api/mobile/browser/logout", headers={"Origin": origin})
                )
                ensure(client.get(origin + "/api/mobile/browser/snapshot"), 401)
                with httpx.Client(follow_redirects=False, timeout=15, trust_env=False) as other:
                    assert (
                        sign_in(other, origin, metadata, email="other@example.test").headers[
                            "location"
                        ]
                        == "/mobile?login=failed"
                    )
                    ensure(other.get(origin + "/api/mobile/browser/snapshot"), 401)
                assert (
                    sign_in(client, origin, metadata, allow=False).headers["location"]
                    == "/mobile?login=failed"
                )
                verifier, native_state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                    .rstrip(b"=")
                    .decode()
                )
                callback = sign_in(
                    client, origin, metadata, native={"challenge": challenge, "state": native_state}
                )
                callback_url = urlsplit(
                    html.unescape(re.search(r'href="([^"]+)"', callback.text)[1])
                )
                assert callback_url.scheme == "vortocode-mobile-auth"
                ticket = parse_qs(callback_url.query)["ticket"][0]
                native_body = {"ticket": ticket, "verifier": verifier, "state": native_state}
                native_credential = ensure(
                    client.post(origin + "/api/mobile/native/exchange", json=native_body)
                ).json()
                ensure(client.post(origin + "/api/mobile/native/exchange", json=native_body), 401)
                if args.swift_probe:
                    result = subprocess.run(
                        [str(args.swift_probe)],
                        input=json.dumps({"origin": origin, "credential": native_credential}),
                        text=True,
                        capture_output=True,
                        timeout=30,
                    )
                    if result.returncode:
                        raise RuntimeError("Swift native API check failed")
                    print(
                        "PASS: real provider PKCE handoff -> Swift native read -> logout revocation",
                        flush=True,
                    )
                else:
                    native_headers = {"Authorization": "Bearer " + native_credential["token"]}
                    ensure(
                        client.get(origin + "/api/mobile/native/snapshot", headers=native_headers)
                    )
                    ensure(
                        client.post(origin + "/api/mobile/native/logout", headers=native_headers)
                    )
                if not args.preview:
                    assert sign_in(client, origin, metadata).headers["location"] == "/mobile"
                    ensure(client.get(origin + "/api/mobile/browser/bootstrap"))
                    bridge.request(
                        port,
                        "DELETE",
                        registration_path,
                        admin,
                        {"revision": registered["revision"], "instance_id": "a" * 32},
                        expected=409,
                    )
                    ensure(client.get(origin + "/api/mobile/browser/snapshot"))
                    bridge.request(
                        port,
                        "DELETE",
                        registration_path,
                        admin,
                        {
                            "revision": registered["revision"],
                            "instance_id": registered["instance_id"],
                        },
                    )
                    ensure(client.get(origin + "/api/mobile/browser/snapshot"), 401)
                print(
                    "PASS: real account login + PKCE callback + cookie-only BFF read; wrong account, denial, CSRF, admin scope, logout and guarded registration checked.",
                    flush=True,
                )
                if args.preview:
                    from fastapi import FastAPI, HTTPException

                    goal_store = PersonalGoals(directory)
                    blocked = goal_store.create({
                        "request_id": "mobile-browsing-test", "objective": "核对 PT 签到连接",
                        "next_action": "检查 OAuth Scope", "acceptance_criteria": ["核对授权"],
                    })
                    goal_store.update(blocked["id"], {"revision": blocked["revision"], "blocker": "等待站点授权"})
                    fixture_app = FastAPI()

                    @fixture_app.get("/credentials")
                    def synthetic_credentials():
                        return {
                            "origin": origin,
                            "email": "owner@example.test",
                            "password": metadata["password"],
                        }

                    @fixture_app.post("/mode")
                    def set_read_fault(payload: dict):
                        if set(payload) != {"mode"} or payload["mode"] not in ("normal", "goals-unavailable", "profile-unavailable", "read-failed", "empty-goals", "logout-failed"):
                            raise HTTPException(status_code=400, detail="Unknown fixture mode")
                        fault["mode"] = payload["mode"]
                        return {"ok": True}

                    @fixture_app.post("/goal-progress")
                    def advance_synthetic_goal(payload: dict):
                        if payload:
                            raise HTTPException(status_code=400, detail="Fixed synthetic goal only")
                        goal = goal_store.get("personal-mobile-oauth-test")
                        goal_store.update(goal["id"], {"revision": goal["revision"], "next_action": "在详情页核对最新进展"})
                        return {"ok": True}

                    preview = Path(directory) / "browser-fixture.json"
                    descriptor = os.open(preview, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "w") as target:
                        json.dump(
                            {
                                "origin": origin,
                                "email": "owner@example.test",
                                "password": metadata["password"],
                            },
                            target,
                        )
                    with bridge.serve(fixture_app) as fixture_port:
                        print(
                            json.dumps(
                                {
                                    "origin": origin,
                                    "fixture_file": str(preview),
                                    "fixture_url": f"http://127.0.0.1:{fixture_port}/credentials",
                                }
                            ),
                            flush=True,
                        )
                        sys.stdin.readline()
        finally:
            os.chdir(previous)
            if child.poll() is None:
                try:
                    child.communicate("stop\n", timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-root", type=Path, default=ROOT.parent / "vortocode-account")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--swift-probe", type=Path)
    main(parser.parse_args())
