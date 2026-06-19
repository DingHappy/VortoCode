"""量化研究 Web API 路由

提供 /api/quant/* 端点，控制量化流水线的启动、停止、状态查询。
提供 /ws/quant WebSocket 端点，实时推送流水线状态。
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Set

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# 模块级状态
_pipeline = None
_pipeline_task: Optional[asyncio.Task] = None
_ws_clients: Set[WebSocket] = set()


class QuantStartRequest(BaseModel):
    """启动请求"""
    mcp_config_path: str = "config/mcp.yaml"
    quant_config_path: str = "config/quant.yaml"
    news_interval: int = 0     # 0 = 用配置文件默认值
    sentiment_interval: int = 0
    factor_interval: int = 0
    review_interval: int = 0


async def _ensure_pipeline(req: Optional[QuantStartRequest] = None) -> "QuantPipeline":
    """获取或创建流水线实例"""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    from src.orchestrator.quant_pipeline import create_quant_pipeline

    intervals = {}
    if req:
        for key, val in {
            "news": req.news_interval,
            "sentiment": req.sentiment_interval,
            "factor": req.factor_interval,
            "review": req.review_interval,
        }.items():
            if val > 0:
                intervals[key] = val

    _pipeline = await create_quant_pipeline(
        mcp_config_path=req.mcp_config_path if req else "config/mcp.yaml",
        quant_config_path=req.quant_config_path if req else "config/quant.yaml",
        intervals=intervals or None,
    )
    return _pipeline


async def _broadcast_status():
    """向所有 WebSocket 客户端推送状态"""
    if not _ws_clients or _pipeline is None:
        return
    status = {
        "type": "quant_status",
        "data": _pipeline.get_status(),
    }
    msg = json.dumps(status, ensure_ascii=False)
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


@router.post("/api/quant/start")
async def start_quant_pipeline(req: QuantStartRequest = QuantStartRequest()):
    """启动量化流水线"""
    global _pipeline_task

    pipeline = await _ensure_pipeline(req)

    if _pipeline_task and not _pipeline_task.done():
        raise HTTPException(status_code=400, detail="Pipeline already running")

    await pipeline.start_all()
    _pipeline_task = asyncio.create_task(_status_broadcaster())

    return {
        "success": True,
        "message": "Quant pipeline started",
        "status": pipeline.get_status(),
    }


@router.post("/api/quant/stop")
async def stop_quant_pipeline():
    """停止量化流水线"""
    global _pipeline_task

    if _pipeline is None:
        raise HTTPException(status_code=400, detail="Pipeline not initialized")

    await _pipeline.stop_all()

    if _pipeline_task and not _pipeline_task.done():
        _pipeline_task.cancel()
        try:
            await _pipeline_task
        except asyncio.CancelledError:
            pass
    _pipeline_task = None

    return {"success": True, "message": "Quant pipeline stopped"}


@router.get("/api/quant/status")
async def get_quant_status():
    """获取量化流水线状态"""
    if _pipeline is None:
        return {"initialized": False, "running": False}

    running = _pipeline_task is not None and not _pipeline_task.done()
    summary = _pipeline.get_summary()
    return {
        "initialized": True,
        "running": running,
        **summary,
    }


@router.get("/api/quant/health")
async def quant_health():
    """健康检查 — 量化平台连接 + 各循环状态"""
    import aiohttp

    checks = {}

    # 1. 检查 quant-platform API 连通性
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get("http://localhost:8000/api/system/info") as resp:
                checks["quant_platform"] = {
                    "status": "ok" if resp.status == 200 else "error",
                    "http_status": resp.status,
                }
    except Exception as e:
        checks["quant_platform"] = {"status": "unreachable", "error": str(e)}

    # 2. 检查流水线状态
    if _pipeline:
        loop_status = _pipeline.get_status()
        for name, info in loop_status.items():
            checks[f"loop_{name}"] = {
                "status": info["status"],
                "iterations": info["iterations"],
            }
    else:
        checks["pipeline"] = {"status": "not_initialized"}

    # 3. 总结
    all_ok = all(
        c.get("status") in ("ok", "running", "idle", "not_initialized")
        for c in checks.values()
    )

    return {
        "healthy": all_ok,
        "checks": checks,
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/api/quant/run/{stage}")
async def run_quant_stage(stage: str):
    """单次执行指定阶段（news/sentiment/factor/review）"""
    pipeline = await _ensure_pipeline()

    stage_map = {
        "news": pipeline._run_news_collection,
        "sentiment": pipeline._run_sentiment_analysis,
        "factor": pipeline._run_factor_research,
        "review": pipeline._run_daily_review,
    }

    if stage not in stage_map:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown stage: {stage}. Valid: {list(stage_map.keys())}",
        )

    result = await stage_map[stage]()
    success = getattr(result, "success", False)
    return {
        "success": success,
        "stage": stage,
        "duration": getattr(result, "duration", 0),
        "results": getattr(result, "results", None),
        "error": getattr(result, "error", None),
    }


@router.get("/api/quant/export")
async def export_quant_results():
    """导出记忆中的量化历史结果"""
    if _pipeline is None:
        raise HTTPException(status_code=400, detail="Pipeline not initialized")

    memory = _pipeline.memory
    export = {}

    stage_queries = {
        "news": "新闻采集",
        "sentiment": "情绪分析",
        "factor": "因子研究",
        "review": "复盘报告",
    }

    for key, query in stage_queries.items():
        items = await memory.retrieve(query, top_k=10)
        export[key] = [
            {
                "content": item.content[:2000],
                "importance": item.importance,
                "timestamp": item.timestamp.isoformat() if hasattr(item, "timestamp") else None,
                "metadata": item.metadata,
            }
            for item in items
        ]

    return {
        "exported_at": datetime.now().isoformat(),
        "token_usage": _pipeline.get_token_usage(),
        "data": export,
    }


@router.websocket("/ws/quant")
async def quant_websocket(ws: WebSocket):
    """WebSocket 实时推送流水线状态"""
    await ws.accept()
    _ws_clients.add(ws)
    try:
        # 立即推送一次当前状态
        if _pipeline:
            await ws.send_text(json.dumps({
                "type": "quant_status",
                "data": _pipeline.get_status(),
            }, ensure_ascii=False))
        # 保持连接，等待客户端消息（ping/pong）
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


async def _status_broadcaster():
    """后台定期广播状态到 WebSocket"""
    try:
        while True:
            await asyncio.sleep(30)
            await _broadcast_status()
    except asyncio.CancelledError:
        return
