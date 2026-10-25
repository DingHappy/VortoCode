"""Loopback HTTP integration host for real goal routes and durable temporary data.

Copies current src without .env, disables runtime lifespan/model construction,
and serves compiled QA assets. --root reuses only a dedicated QA directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--assets", required=True)
parser.add_argument("--root")
parser.add_argument("--port", type=int, default=0)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[2]
assets = Path(args.assets).resolve()
root = Path(args.root).resolve() if args.root else Path(tempfile.mkdtemp(prefix="vorto-goal-integration-", dir="/tmp")).resolve()
if root.parent != Path("/tmp").resolve() or not root.name.startswith("vorto-goal-integration-") or root.is_symlink():
    raise SystemExit("Only dedicated temporary QA roots are allowed")
root.mkdir(exist_ok=True)
runtime = root / "runtime"
if not runtime.exists():
    shutil.copytree(repo / "src", runtime / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
data = root / "workspace"
data.mkdir(exist_ok=True)
os.chdir(data)
for key in list(os.environ):
    if key.startswith(("VORTOCODE_", "AUTODEV_", "OPENAI_", "LLM_", "ANTHROPIC_")) or key == "DEFAULT_MODEL":
        os.environ.pop(key, None)
# Public fixture token, never a user credential.
os.environ.update(VORTOCODE_WORKSPACE_SCOPE="general", VORTOCODE_API_TOKEN="goal-integration-only",
                  OPENAI_API_KEY="", VORTOCODE_ENABLE_SHELL="0", VORTOCODE_ENABLE_BROWSER="0",
                  VORTOCODE_IM="", VORTOCODE_OWNER_NOTIFY_URL="", VORTOCODE_CRON="0", VORTOCODE_HEARTBEAT="0",
                  VORTOCODE_USAGE_DIR=str(root / "usage"))
sys.path.insert(0, str(runtime))

from src.llm.client import LLMClient  # noqa: E402

model_attempts = []
def reject_model(*_args, **_kwargs):
    model_attempts.append("blocked")
    raise RuntimeError("Model construction is forbidden in goal integration QA")

LLMClient.__init__ = reject_model

from fastapi import FastAPI, HTTPException, WebSocket  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from src.web.server import app as production  # noqa: E402
from src.web.auth import is_authed  # noqa: E402
from src.workflow.workbench.personal_goals import PersonalGoals  # noqa: E402

app = FastAPI()
fault = {"next_write": "none", "next_read": False}
traffic_path = root / "traffic.json"
traffic = json.loads(traffic_path.read_text()) if traffic_path.exists() else []
goal_path = re.compile(r"^/api/assistant/goals(?:/personal-[A-Za-z0-9_-]{1,64}(?:/criteria/[^/]+/evidence|/follow-up(?:/acknowledge)?)?)?$")


@app.middleware("http")
async def isolate_and_observe(request, call_next):
    path, method = request.url.path, request.method
    if path.startswith(("/qa/", "/assets/")):
        return await call_next(request)
    allowed = goal_path.fullmatch(path) or path in {"/api/codex/status", "/api/goals"}
    if not allowed:
        return JSONResponse({"detail": "QA host only exposes goal-chain routes"}, status_code=403)
    if method == "GET" and path == "/api/assistant/goals" and fault["next_read"] and is_authed(request):
        fault["next_read"] = False
        traffic.append({"method": method, "path": path, "status": 503, "fault": "read-back"})
        traffic_path.write_text(json.dumps(traffic, indent=2))
        return JSONResponse({"detail": "QA 回读故障，请刷新核对"}, status_code=503)
    response = await call_next(request)
    upstream = response.status_code
    mode = "none"
    if goal_path.fullmatch(path) and method in {"POST", "PATCH", "PUT"} and upstream == 200:
        mode, fault["next_write"] = fault["next_write"], "none"
        if mode == "read-back":
            fault["next_read"] = True
        if mode == "lost":
            # The real route has completed before its response is replaced.
            async for _chunk in response.body_iterator:
                pass
            response = JSONResponse({"detail": "QA 写入后回执故障，请读取当前目标核对"}, status_code=503)
    traffic.append({"method": method, "path": path, "upstream": upstream, "status": response.status_code, "fault": mode})
    traffic_path.write_text(json.dumps(traffic, indent=2))
    return response


@app.post("/qa/fault")
def set_fault(body: dict):
    mode = body.get("next_write", "none")
    if mode not in {"none", "lost", "read-back"}:
        raise HTTPException(400, "Unsupported QA fault")
    fault.update(next_write=mode, next_read=False)
    return dict(fault)


@app.get("/qa/evidence")
def evidence():
    goals = PersonalGoals(str(data)).list()
    return {"root": str(root), "model_attempts": len(model_attempts), "traffic": traffic,
            "goals": [{"id": goal["id"], "revision": goal["revision"], "status": goal["status"],
                       "criteria": len(goal["acceptance_criteria"]), "evidence": len(goal["evidence"])} for goal in goals],
            "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted((data / ".vortocode" / "goals").glob("personal-*.json"))}}


@app.websocket("/ws")
async def no_agent_socket(websocket: WebSocket):
    await websocket.close(code=1008)


app.mount("/assets", StaticFiles(directory=str(assets / "assets")), name="qa-assets")
app.mount("/qa", StaticFiles(directory=str(assets / "qa"), html=True), name="qa-pages")
app.mount("/", production)
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", args.port))
listener.listen(128)
port = listener.getsockname()[1]
metadata = {"root": str(root), "port": port, "pid": os.getpid(), "lifespan": "off", "source_hashes": {
    name: hashlib.sha256((runtime / name).read_bytes()).hexdigest() for name in [
        "src/web/server.py", "src/web/routers/personal_goals.py", "src/workflow/workbench/personal_goals.py", "src/workflow/workbench/goals.py"]}}
(root / "host.json").write_text(json.dumps(metadata, indent=2))
print(json.dumps(metadata), flush=True)
import uvicorn  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=port, fd=listener.fileno(), lifespan="off", access_log=False, log_level="warning", loop="asyncio")
