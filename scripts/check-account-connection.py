"""Real account-service -> Gateway -> TS reader acceptance, in temporary roots.

Requires the existing sibling account repo/venv. No real account, login cookie,
model, scheduler or production database is used. Private child stdout/stdin is
an IPC channel for synthetic credentials; do not run --provider directly.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def serve(app, *, port=0):
    import uvicorn

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, lifespan="off", log_level="error", access_log=False)
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                thread.join(0.02)
            if not server.started:
                raise RuntimeError("Temporary service failed to start")
            yield port
        finally:
            server.should_exit = True
            thread.join(10)
            if thread.is_alive():
                raise RuntimeError("Temporary service failed to stop")


def provider(args):
    database_path = Path(args.database).resolve()
    if sys.stdout.isatty() or not database_path.is_relative_to(
        Path(tempfile.gettempdir()).resolve()
    ):
        raise RuntimeError("Provider fixture requires private IPC and a temporary database")
    sys.path.insert(0, str(args.account_root))
    from account.config import Settings
    from account.db import Database
    from account.models import User
    from account import service
    from account.web import create_app
    from account.security import hash_password
    import account.web as account_web

    # Fixture-only names avoid overwriting user account cookies on loopback.
    account_web.SESSION_COOKIE = "vorto_fixture_session_" + secrets.token_hex(8)
    account_web.CSRF_COOKIE = "vorto_fixture_csrf_" + secrets.token_hex(8)
    password = secrets.token_urlsafe(32)

    class NoMail:
        def send(self, *args, **kwargs):
            raise RuntimeError("Mail is forbidden in this fixture")

    settings = Settings(
        database_url=f"sqlite:///{database_path}",
        public_url="http://127.0.0.1",
        secret_key=secrets.token_urlsafe(32),
        redis_url="",
        smtp_host="",
        smtp_port=25,
        smtp_user="",
        smtp_password="",
        smtp_from="fixture@example.test",
        registration="closed",
        mobile_redirect_uris=(args.callback,) if getattr(args, "callback", "") else (),
    )
    database = Database(settings.database_url)
    app = create_app(settings, database=database, mailer=NoMail())
    tokens = {}
    with database.session() as db:
        users = []
        for label in ("owner", "other"):
            user = User(
                email=f"{label}@example.test",
                password_hash=hash_password(password),
                status="active",
                created_at=service.utcnow(),
                email_verified_at=service.utcnow(),
            )
            db.add(user)
            db.flush()
            users.append(user)
        for label, user in (("desktop", users[0]), ("phone", users[0]), ("other", users[1])):
            verifier = secrets.token_urlsafe(32)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            redirect = "http://127.0.0.1:53123/callback"
            code = service.create_auth_code(
                db, user, code_challenge=challenge, redirect_uri=redirect, device_name=label
            )
            pair = service.exchange_code(
                db, code=code, code_verifier=verifier, redirect_uri=redirect
            )
            tokens[label] = pair.access_token
    with serve(app) as port:
        print(
            json.dumps({"port": port, "password": password, **tokens}), flush=True
        )  # private pipe only, never the tool output
        sys.stdin.readline()


def request(port, method, path, bearer, body=None, expected=200):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(
            method,
            path,
            body=json.dumps(body) if body is not None else None,
            headers={"Authorization": "Bearer " + bearer, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != expected:
            raise RuntimeError(f"Unexpected {response.status} at {method} {path}")
        return json.loads(response.read())
    finally:
        connection.close()


def run(args):
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="vorto-account-connection-") as directory:
        child = subprocess.Popen(
            [
                str(args.account_python),
                str(Path(__file__).resolve()),
                "--provider",
                "--account-root",
                str(args.account_root),
                "--database",
                str(Path(directory) / "account.sqlite"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        try:
            metadata = json.loads(child.stdout.readline())
            os.chdir(directory)
            sys.path.insert(0, str(ROOT))
            admin = secrets.token_urlsafe(32)
            os.environ.update(
                VORTOCODE_API_TOKEN=admin,
                VORTOCODE_WORKSPACE_SCOPE="general",
                VORTOCODE_ACCOUNT_URL=f"http://127.0.0.1:{metadata['port']}",
            )
            from src.web.server import app
            from src.memory.assistant_profile import save_profile
            from src.gateway.main_conversation import claim_main_chat
            from src.workflow.workbench.personal_goals import PersonalGoals

            save_profile(directory, name="Smoke assistant")
            claim_main_chat(directory, "sid-smoke-main")
            PersonalGoals(directory).create(
                {
                    "request_id": "account-acceptance",
                    "objective": "Account read",
                    "next_action": "Inspect",
                    "acceptance_criteria": ["User verifies"],
                }
            )
            with serve(app) as port:
                path = "/api/mobile/account/registration"
                registered = request(
                    port,
                    "PUT",
                    path,
                    admin,
                    {
                        "access_token": metadata["desktop"],
                        "issuer": os.environ["VORTOCODE_ACCOUNT_URL"],
                        "name": "Account assistant",
                        "revision": 0,
                    },
                )
                resource = "vcm_" + secrets.token_urlsafe(32)
                body = {
                    "access_token": metadata["phone"],
                    "issuer": os.environ["VORTOCODE_ACCOUNT_URL"],
                    "name": "Account phone",
                    "request_id": uuid.uuid4().hex,
                    "device_token": resource,
                    "registration_revision": registered["revision"],
                }
                grant = request(port, "POST", "/api/mobile/account/connect", admin, body)
                assert grant == request(port, "POST", "/api/mobile/account/connect", admin, body)
                request(
                    port,
                    "POST",
                    "/api/mobile/account/connect",
                    admin,
                    {**body, "access_token": metadata["other"]},
                    expected=403,
                )
                request(
                    port,
                    "POST",
                    "/api/mobile/account/connect",
                    metadata["phone"],
                    body,
                    expected=401,
                )
                print(
                    "Real provider identity: same account accepted; other account and untrusted bridge rejected."
                )
                configuration = {
                    "origin": f"http://127.0.0.1:{port}",
                    "instance_id": grant["instance_id"],
                    "token": resource,
                }
                command = [
                    "node",
                    "--experimental-strip-types",
                    str(ROOT / "packages/mobile-client/scripts/contract-smoke.ts"),
                ]

                def reader(revoked=False):
                    result = subprocess.run(
                        command,
                        input=json.dumps({**configuration, "revoked": revoked}),
                        text=True,
                        capture_output=True,
                        cwd=ROOT,
                        timeout=20,
                    )
                    if result.returncode:
                        raise RuntimeError("Account TS read check failed")

                reader()
                request(metadata["port"], "POST", "/api/logout", metadata["phone"])
                request(
                    port,
                    "POST",
                    "/api/mobile/account/connect",
                    admin,
                    {**body, "previous_expires_at": grant["device"]["expires_at"]},
                    expected=401,
                )
                request(port, "DELETE", path, admin, {"revision": registered["revision"]})
                reader(revoked=True)
                print(
                    "Scoped TS read passed; provider logout blocks renewal; owner unbind blocks the next read."
                )
        finally:
            os.chdir(original)
            if child.poll() is None:
                try:
                    child.communicate("stop\n", timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-root", type=Path, default=ROOT.parent / "vortocode-account")
    parser.add_argument("--account-python", type=Path)
    parser.add_argument("--provider", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--database", help=argparse.SUPPRESS)
    parser.add_argument("--callback", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.account_python = args.account_python or args.account_root / ".venv/bin/python"
    provider(args) if args.provider else run(args)
