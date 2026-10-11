"""Isolated Inbox/receipt acceptance host; no agent runtime or model execution.

Run from the repo: python desktop/qa/task-board-server.py --assets /tmp/assets
Copies src into a temporary runtime, uses only a temporary durable workspace,
and mounts the original read-only Inbox + manual receipt routers. No installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--assets", required=True)
parser.add_argument("--port", type=int, default=0)
parser.add_argument("--root", help="Reuse an existing QA root for an explicit restart")
args = parser.parse_args()
repo = Path(__file__).resolve().parents[2]
assets = Path(args.assets).resolve()
root = Path(args.root).resolve() if args.root else Path(tempfile.mkdtemp(prefix="vorto-board-lifecycle-", dir="/tmp"))
if not str(root).startswith("/private/tmp/vorto-board-lifecycle-") and not str(root).startswith("/tmp/vorto-board-lifecycle-"):
    raise SystemExit("Only dedicated temporary QA roots are allowed")
runtime = root / "runtime"
if not runtime.exists():
    shutil.copytree(repo / "src", runtime / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
data = root / "workspace"
data.mkdir(exist_ok=True)
os.chdir(data)
os.environ["VORTOCODE_WORKSPACE_SCOPE"] = "project"
sys.path.insert(0, str(runtime))

from src.llm.client import LLMClient  # noqa: E402
model_calls = []
def reject_model(*a, **kw):
    model_calls.append("unexpected constructor")
    raise RuntimeError("QA model execution forbidden")
LLMClient.__init__ = reject_model

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from src.workflow.tasks.tasks import BackgroundTask, TaskLedger, TASK_STATE_LOCK  # noqa: E402
from src.workflow.tasks.handoffs import revision  # noqa: E402
from src.workflow.tasks.task_dependencies import _input, invalidate  # noqa: E402
from src.workflow.workbench.goals import GoalLedger  # noqa: E402
from src.gateway.session_store import save_session  # noqa: E402
from src.web.routers import decisions, task_inbox, goals  # noqa: E402

ledger = TaskLedger(str(data))
def seed_task(name, review="accepted", kind="delegation"):
    value = BackgroundTask.new(kind, name, tid="task-" + name, owner_session="sid-owner", goal_id="")
    value.status, value.result = "done", "result " + name
    if kind == "delegation":
        value.collaboration = {"round": 1, "assignee": "research", "review": review,
                              "acceptance": ["evidence"], "tainted": False, "dispatch": {"source": "api"}}
    return value

def receipt(value):
    value.handoff_receipt = {"revision": revision(value), "note": "QA checked", "handled_at": "2026-10-07T00:00:00+00:00"}
    return value

if not ledger.load("task-parent"):
    parent = seed_task("parent")
    child = seed_task("child")
    refs = [{"task_id": parent.id, "round": 1}]
    child.collaboration["dispatch"]["depends_on"] = refs
    child.dependencies = {"version": 1, "task_id": child.id, "owner_session": child.owner_session,
                          "requires": refs, "resolution": "consumed", "released_round": 1, "inputs": [_input(parent)]}
    receipt(child)
    missing = seed_task("omitted")
    uncertain = seed_task("unknown")
    uncertain.collaboration["dispatch"]["depends_on"] = [{"task_id": missing.id, "round": 1}]
    uncertain.dependencies = {"version": 1, "task_id": uncertain.id, "owner_session": uncertain.owner_session,
        "requires": uncertain.collaboration["dispatch"]["depends_on"], "resolution": "consumed", "released_round": 1, "inputs": [_input(missing)]}
    receipt(uncertain)
    for value in [parent, child, receipt(seed_task("dev", kind="dev")), seed_task("pending", review="pending"), uncertain]:
        assert ledger.save(value)
    assert save_session(str(data), "sid-owner", [], [], None, title="实际 owner 会话")
    GoalLedger(str(data)).create("保持目标与任务完成独立", ["人工核对交付"])

app = FastAPI()
app.include_router(decisions.router)
app.include_router(task_inbox.router)
app.include_router(goals.router)

@app.post("/qa/change-parent")
def change_parent():
    with TASK_STATE_LOCK:
        parent = ledger.load("task-parent")
        parent.result += " external change"
        assert ledger.save(parent)
    return {"changed": True}

@app.post("/qa/latch-child")
def latch_child():
    with TASK_STATE_LOCK:
        child = ledger.load("task-child")
        if invalidate(child, ledger):
            assert ledger.save(child)
    return {"latched": True}

@app.get("/qa/evidence")
def evidence():
    files = sorted((data / ".vortocode" / "tasks").glob("*.json"))
    return {"root": str(root), "model_calls": model_calls, "goal_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((data / ".vortocode" / "goals").glob("*.json"))}, "task_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}

app.mount("/", StaticFiles(directory=str(assets), html=True), name="fixture-assets")
listener = socket.socket()
listener.bind(("127.0.0.1", args.port))
listener.listen(128)
port = listener.getsockname()[1]
metadata = {"root": str(root), "port": port, "pid": os.getpid(), "runtime_files": {
    name: hashlib.sha256((runtime / name).read_bytes()).hexdigest() for name in ["src/workflow/tasks/task_summary.py", "src/workflow/tasks/runtime_inbox.py", "src/workflow/tasks/handoffs.py", "src/web/routers/decisions.py"]}}
(root / "host.json").write_text(json.dumps(metadata, indent=2))
print(json.dumps(metadata), flush=True)
import uvicorn  # noqa: E402
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", workers=1, loop="asyncio", fd=listener.fileno())
