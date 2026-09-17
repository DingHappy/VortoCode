"""Real Chromium + production review UI/router/store; deterministic products, no LLM or IM.

VORTOCODE_LIVE_BROWSER=1 python -m pytest tests/live/test_pipeline_browser_workflow.py -q
"""
import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from src.browser.verify import run_browser_probe
from src.gateway.pipeline import PipelineDef, PipelineStore, advance
from src.gateway.products import ProductStore

pytestmark = pytest.mark.skipif(os.getenv("VORTOCODE_LIVE_BROWSER") != "1",
                              reason="requires opt-in real Chromium and loopback listener")


@pytest.fixture
def review_site(tmp_path, monkeypatch):
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, Response, FileResponse
    from src.web.routers import pipelines

    definition = PipelineDef.from_dict({"name": "browser-eval", "stages": [
        {"id": "draft", "produces": "article", "review": True},
    ]})
    store = PipelineStore(str(tmp_path))
    run = store.start(definition)

    async def fixture_output(stage, inputs):
        return {"payload": {"title": "待审稿件", "body": "这是要审阅的完整正文。"}, "tokens": 10}

    asyncio.run(advance(str(tmp_path), run.run_id, definition=definition, execute=fixture_output))
    monkeypatch.setattr(pipelines, "_repo", lambda: str(tmp_path))
    app = FastAPI()
    app.include_router(pipelines.router)
    html = (Path(__file__).resolve().parents[2] / "web" / "review.html").read_text()

    @app.get("/review", response_class=HTMLResponse)
    def review_page():
        return html

    @app.get("/api/auth/status")
    def auth_status():
        return {"auth_required": False, "authed": True}

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/pwa-icon.svg")
    def pwa_icon():
        return FileResponse(Path(__file__).resolve().parents[2] / "web" / "pwa-icon.svg")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        yield f"http://127.0.0.1:{port}/review", store, run
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


def _select(run):
    return {"action": "click", "target": {"text": run.run_id}}


def _assert_text(text):
    return {"action": "assert_visible", "target": {"text": text}}


@pytest.mark.parametrize("verdict", ["approve", "reject"])
def test_review_survives_reload(review_site, tmp_path, verdict):
    url, store, run = review_site
    steps = [_select(run), {"action": "assert_visible", "target": {"role": "button", "name": "批准"}}]
    if verdict == "approve":
        steps.append({"action": "click", "target": {"role": "button", "name": "批准"}})
        expected = "当前没有等你批的工序。"
    else:
        steps.extend([
            {"action": "fill", "target": {"label": "审批意见"}, "value": "补充数据来源"},
            {"action": "click", "target": {"role": "button", "name": "驳回重做"}},
        ])
        expected = "驳回重做：补充数据来源"
    steps.extend([_assert_text(expected), {"action": "reload"}, _select(run), _assert_text(expected)])
    result = run_browser_probe({"url": url, "steps": steps}, tmp_path / f"{verdict}.png", 15)
    (tmp_path / f"{verdict}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert result["ok"], result["output"]
    assert result["verification_kind"] == "workflow"
    assert len(result["steps"]) == len(steps) and all(s["ok"] for s in result["steps"])
    assert Path(result["screenshot_path"]).stat().st_size > 0
    assert Path(result["trace_path"]).stat().st_size > 0
    saved = store.load(run.run_id)
    assert saved.stages[0].status == ("done" if verdict == "approve" else "pending")
    assert len(saved.reviews) == 1 and saved.reviews[0]["verdict"] == verdict
    if verdict == "reject":
        note = ProductStore(store.repo_root).load(saved.stages[0].extra_inputs[-1])
        assert note.payload["comment"] == "补充数据来源"


def test_failed_assertion_keeps_screenshot_and_trace(review_site, tmp_path):
    url, _, run = review_site
    result = run_browser_probe({"url": url, "steps": [
        _select(run), _assert_text("不会出现的验收结果"),
        {"action": "click", "target": {"role": "button", "name": "批准"}},
    ]}, tmp_path / "failed.png", 3)
    assert not result["ok"] and "第 2 步" in result["output"]
    assert len(result["steps"]) == 2 and not result["steps"][1]["ok"]
    assert Path(result["screenshot_path"]).stat().st_size > 0
    assert Path(result["trace_path"]).stat().st_size > 0
