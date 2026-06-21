"""realtime 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# WebSocket
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点"""
    from src.web.auth import ws_token_ok
    if not ws_token_ok(websocket):
        await websocket.close(code=1008)  # policy violation
        return
    await manager.connect(websocket)
    
    try:
        # 发送当前状态
        await websocket.send_json({
            "type": "init",
            "data": state.to_dict()
        })
        
        # 监听消息
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # 处理客户端消息
            await handle_websocket_message(websocket, message)
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        _WS_AGENTS.pop(id(websocket), None)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)
        _WS_AGENTS.pop(id(websocket), None)


async def handle_websocket_message(websocket: WebSocket, message: Dict[str, Any]):
    """处理 WebSocket 消息"""
    msg_type = message.get("type")

    if msg_type == "ping":
        await websocket.send_json({"type": "pong"})

    elif msg_type == "get_status":
        await websocket.send_json({
            "type": "status",
            "data": state.to_dict()
        })

    elif msg_type == "agent":
        await handle_agent_message(websocket, message)


# 每个 WebSocket 连接一个主 agent（含多轮上下文）；断开时清理
_WS_AGENTS: Dict[int, Any] = {}


def _ws_agent(websocket):
    key = id(websocket)
    agent = _WS_AGENTS.get(key)
    if agent is None:
        import os
        from src.agents.main_agent import MainAgent, build_read_tools
        from src.web.artifacts import build_artifact_tools
        # 制品(publish_artifact)是写工具：Web 无模态确认，靠 build 模式门控「人在关口」。
        tools = build_read_tools(os.getcwd()) + build_artifact_tools(os.getcwd())
        agent = MainAgent(tools)
        _WS_AGENTS[key] = agent
    return agent


async def handle_agent_message(websocket, message: Dict[str, Any]):
    """在 Web 端驱动主 agent（只读工具）并把事件流式发回前端。

    事件类型：agent_say(工具提示) / agent_stream(增量) / agent_emit(成段输出) /
    agent_error / agent_done。前端据此渲染对话与流式。
    """
    import asyncio
    import os

    text = str(message.get("text", "")).strip()
    if not text:
        await websocket.send_json({"type": "agent_error", "text": "空输入"})
        return
    if not os.getenv("OPENAI_API_KEY"):
        await websocket.send_json({"type": "agent_emit", "text": "未配置 OPENAI_API_KEY，无法对话。"})
        await websocket.send_json({"type": "agent_done"})
        return

    mode = message.get("mode", "plan")
    agent = _ws_agent(websocket)
    q: asyncio.Queue = asyncio.Queue()

    def agent_say(m):
        try:                                  # 剥掉 Rich 标记，Web 端不显示 [b]/[dim] 等原文
            from rich.text import Text as _Rt
            m = _Rt.from_markup(str(m)).plain
        except Exception:  # noqa: BLE001
            pass
        q.put_nowait({"type": "agent_say", "text": m})

    def agent_emit(m):
        q.put_nowait({"type": "agent_emit", "text": m})

    def agent_stream(p):
        q.put_nowait({"type": "agent_stream", "text": p})

    async def _run():
        try:
            await agent.run_turn(text, mode=mode, say=agent_say,
                                 emit=agent_emit, stream_cb=agent_stream)
        except Exception as e:  # noqa: BLE001
            q.put_nowait({"type": "agent_error", "text": str(e)})
        finally:
            q.put_nowait(None)

    task = asyncio.create_task(_run())
    while True:
        evt = await q.get()
        if evt is None:
            break
        await websocket.send_json(evt)
    await task
    await websocket.send_json({"type": "agent_done"})
