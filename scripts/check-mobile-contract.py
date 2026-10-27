"""Loopback HTTP acceptance in an isolated temporary gateway; no runtime workers.

Uses the actual application, auth/workspace middleware and mobile routes, but
disables lifespan so models, schedulers and host services never start. Grants
travel to the TS client over private stdin and are destroyed with the temp dir.
"""

from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="vorto-mobile-contract-") as directory:
        os.chdir(directory)
        try:
            admin = secrets.token_urlsafe(32)
            os.environ["VORTOCODE_API_TOKEN"] = admin
            os.environ["VORTOCODE_WORKSPACE_SCOPE"] = "general"
            from src.gateway.main_conversation import claim_main_chat
            from src.memory.assistant_profile import save_profile
            from src.workflow.workbench.personal_goals import PersonalGoals
            from src.web.server import app
            import uvicorn

            save_profile(directory, name="Smoke assistant")
            claim_main_chat(directory, "sid-smoke-main")
            PersonalGoals(directory).create(
                {
                    "request_id": "smoke",
                    "objective": "Read-only contract",
                    "acceptance_criteria": ["User verifies evidence"],
                    "next_action": "Inspect",
                }
            )
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
                server = uvicorn.Server(
                    uvicorn.Config(app, lifespan="off", log_level="error", access_log=False)
                )
                thread = threading.Thread(
                    target=server.run, kwargs={"sockets": [listener]}, daemon=True
                )
                thread.start()
                try:
                    deadline = time.monotonic() + 10
                    while not server.started and thread.is_alive() and time.monotonic() < deadline:
                        thread.join(0.02)
                    if not server.started:
                        raise RuntimeError("Temporary gateway did not start")

                    def request(method, path, body=None):
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                        try:
                            connection.request(
                                method,
                                path,
                                body=json.dumps(body) if body is not None else None,
                                headers={
                                    "Authorization": f"Bearer {admin}",
                                    "Content-Type": "application/json",
                                },
                            )
                            response = connection.getresponse()
                            if response.status != 200:
                                raise RuntimeError(f"Provisioning HTTP status {response.status}")
                            return json.loads(response.read())
                        finally:
                            connection.close()

                    grant = request("POST", "/api/mobile/devices", {"name": "Contract phone"})
                    configuration = {
                        "origin": f"http://127.0.0.1:{port}",
                        "instance_id": grant["instance_id"],
                        "token": grant["token"],
                    }
                    command = [
                        "node",
                        "--experimental-strip-types",
                        str(ROOT / "packages/mobile-client/scripts/contract-smoke.ts"),
                    ]
                    for revoked in (False, True):
                        if revoked:
                            request("DELETE", f"/api/mobile/devices/{grant['device']['id']}")
                        result = subprocess.run(
                            command,
                            input=json.dumps({**configuration, "revoked": revoked}),
                            text=True,
                            capture_output=True,
                            timeout=20,
                            cwd=ROOT,
                        )
                        if result.returncode:
                            # A failed third-party stack may include request internals. Keep credentials private.
                            raise RuntimeError(
                                "TypeScript HTTP contract check failed; inspect the isolated fixture"
                            )
                        print(result.stdout.strip())
                    request("POST", "/api/mobile/pairing-window", {"enabled": True})
                    phone = subprocess.Popen(
                        [
                            "node",
                            "--experimental-strip-types",
                            str(ROOT / "packages/mobile-client/scripts/pairing-smoke.ts"),
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        cwd=ROOT,
                    )
                    try:
                        phone.stdin.write(json.dumps({"origin": configuration["origin"]}) + "\n")
                        phone.stdin.flush()
                        pairing = json.loads(phone.stdout.readline())
                        request(
                            "POST",
                            f"/api/mobile/pairings/{pairing['id']}/decision",
                            {"code": pairing["code"], "approve": True},
                        )
                        phone.stdin.write("approved\n")
                        phone.stdin.flush()
                        if phone.stdout.readline().strip() != "read-verified":
                            raise RuntimeError("Pairing read contract failed")
                        request("DELETE", f"/api/mobile/devices/{pairing['id']}")
                        phone.stdin.write("revoked\n")
                        phone.stdin.flush()
                        if phone.stdout.readline().strip() != "revoked-verified":
                            raise RuntimeError("Pairing revocation contract failed")
                        phone.wait(timeout=10)
                        if phone.returncode:
                            raise RuntimeError("Pairing client failed")
                        print(
                            "Two-sided pairing, protected read and revocation passed real HTTP checks."
                        )
                    finally:
                        if phone.poll() is None:
                            phone.kill()
                            phone.wait()
                finally:
                    server.should_exit = True
                    thread.join(10)
                    if thread.is_alive():
                        raise RuntimeError("Temporary gateway did not stop")
        finally:
            os.chdir(original)


if __name__ == "__main__":
    main()
