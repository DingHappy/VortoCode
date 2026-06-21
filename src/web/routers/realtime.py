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
        _cancel_agent_turn(websocket)                  # 断开即中断在跑的回合
        _WS_AGENTS.pop(id(websocket), None)
        _WS_AGENT_TASKS.pop(id(websocket), None)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)
        _cancel_agent_turn(websocket)
        _WS_AGENTS.pop(id(websocket), None)
        _WS_AGENT_TASKS.pop(id(websocket), None)


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

    elif msg_type == "agent_cancel":          # 「停止」：中断正在跑的回合（若有）
        _cancel_agent_turn(websocket)


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


# 每个连接同时只跑一个回合；记下任务，供「停止」(agent_cancel)与断开时取消
_WS_AGENT_TASKS: Dict[int, Any] = {}


def _cancel_agent_turn(websocket) -> bool:
    """取消该连接正在跑的回合（若有）。返回是否真的发起了取消。"""
    task = _WS_AGENT_TASKS.get(id(websocket))
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


async def handle_agent_message(websocket, message: Dict[str, Any]):
    """启动一个 agent 回合（**后台任务**），随即返回——好让接收循环在回合执行期间
    仍能收到新消息（尤其是 agent_cancel「停止」）。回合事件由 _run_agent_turn 发回。
    """
    import os

    text = str(message.get("text", "")).strip()
    if not text:
        await websocket.send_json({"type": "agent_error", "text": "空输入"})
        return
    if not os.getenv("OPENAI_API_KEY"):
        await websocket.send_json({"type": "agent_emit", "text": "未配置 OPENAI_API_KEY，无法对话。"})
        await websocket.send_json({"type": "agent_done"})
        return

    existing = _WS_AGENT_TASKS.get(id(websocket))
    if existing is not None and not existing.done():       # 不并发：上一条还在跑就提示
        await websocket.send_json({"type": "agent_error", "text": "上一条还在跑，先等它结束或点「停止」。"})
        return

    mode = message.get("mode", "plan")
    _WS_AGENT_TASKS[id(websocket)] = asyncio.create_task(
        _run_agent_turn(websocket, text, mode))


async def _run_agent_turn(websocket, text: str, mode: str):
    """实际跑一个回合：run_turn 产出的事件经队列串行发回前端；整个任务可被取消（中断）。

    事件类型：agent_say(工具提示) / agent_stream(增量) / agent_emit(成段输出) /
    agent_error / agent_done / agent_cancelled。
    """
    import contextlib

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
        except asyncio.CancelledError:        # 中断：直接上抛，不当成错误
            raise
        except Exception as e:  # noqa: BLE001
            q.put_nowait({"type": "agent_error", "text": str(e)})
        finally:
            q.put_nowait(None)

    inner = asyncio.create_task(_run())
    try:
        while True:
            evt = await q.get()
            if evt is None:
                break
            await websocket.send_json(evt)
        await inner
        await websocket.send_json({"type": "agent_done"})
    except asyncio.CancelledError:            # 收到「停止」：连同在飞的 LLM 调用一起取消
        inner.cancel()
        # 注意 CancelledError 是 BaseException，suppress(Exception) 抓不到，必须显式列出
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await inner
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await websocket.send_json({"type": "agent_cancelled", "text": "已中断"})
        # 故意吞掉 CancelledError：这是一次性后台任务，优雅收尾即可
    finally:
        if _WS_AGENT_TASKS.get(id(websocket)) is asyncio.current_task():
            _WS_AGENT_TASKS.pop(id(websocket), None)
