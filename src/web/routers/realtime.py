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
        await _replay_history(websocket)        # 重连/刷新：回放该会话之前的对话

        # 监听消息
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # 处理客户端消息
            await handle_websocket_message(websocket, message)
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        _cancel_agent_turn(websocket)                  # 断开即中断在跑的回合
        _WS_AGENT_TASKS.pop(_session_key(websocket), None)
        # 保留 _SESSIONS[key]（agent + transcript）以便重连恢复；靠 _MAX_SESSIONS 淘汰
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)
        _cancel_agent_turn(websocket)
        _WS_AGENT_TASKS.pop(_session_key(websocket), None)


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

    elif msg_type == "agent_confirm_response":  # 前端对 agent_confirm 的应答 → 解开等待的工具
        fut = _PENDING_CONFIRMS.get(message.get("id"))
        if fut is not None and not fut.done():
            fut.set_result(bool(message.get("ok")))

    elif msg_type == "agent_tts":             # 「🔊 播放」：把某条回复合成成语音回传前端播放
        await handle_tts_message(websocket, message)


# 等待前端确认的工具：confirm id → Future（前端 agent_confirm_response 来了就 set_result）
_PENDING_CONFIRMS: Dict[str, Any] = {}


def _make_ws_confirm(websocket, q):
    """造一个 async confirm(message)->bool：发 agent_confirm 事件给前端、等其应答。

    事件经 q 走（与其它事件同序、由 drain 循环发出）；接收循环并发处理应答（#29 起回合不阻塞收消息）。
    超时/出错/取消都安全返回 False（不放行）。
    """
    async def _confirm(message: str) -> bool:
        import uuid
        cid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        _PENDING_CONFIRMS[cid] = fut
        q.put_nowait({"type": "agent_confirm", "id": cid, "text": str(message)})
        try:
            return bool(await asyncio.wait_for(fut, timeout=300))
        except Exception:  # noqa: BLE001  # 超时/取消 → 拒绝（安全）
            return False
        finally:
            _PENDING_CONFIRMS.pop(cid, None)
    return _confirm


# ---- 会话级持久化：按客户端 sid 让 agent 跨重连/刷新存活，另留一份干净的展示用 transcript ----
# 以前 agent 按 id(websocket) 存活、断开即丢，刷新就重来。现在按 sid（前端 sessionStorage，
# 刷新仍在）存活，重连后回放对话。靠 _MAX_SESSIONS 上限淘汰最久未活动者，防泄漏。
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_MAX_SESSIONS = 50
_MAX_TRANSCRIPT = 200


def _session_key(websocket) -> str:
    """稳定会话键：优先客户端 ?sid=（刷新仍在），否则退回连接 id（旧行为，单连接生命周期）。"""
    try:
        sid = websocket.query_params.get("sid")
    except Exception:  # noqa: BLE001
        sid = None
    return f"sid-{sid}" if sid else f"ws-{id(websocket)}"


def _new_agent():
    import os
    from src.agents.main_agent import (MainAgent, build_command_tool,
                                        build_dev_tools, build_pr_tool, build_read_tools,
                                        build_research_tools, build_web_tools)
    from src.web.artifacts import build_artifact_tools
    # 制品/dev_isolated 靠隔离 + build 门控；run_command 高危 → 走 WS 确认（confirm_holder 每回合
    # 重绑到当前连接，见 _run_agent_turn）。无回合上下文时 confirm 默认拒绝。
    cwd = os.getcwd()
    confirm_holder = {"fn": None}
    progress_holder = {"fn": None}                 # dev 流水线进度 → 每回合重绑到当前 ws 的 agent_say

    async def _confirm(message: str) -> bool:
        fn = confirm_holder["fn"]
        return bool(await fn(message)) if fn is not None else False

    def _progress(msg: str) -> None:               # 同步、best-effort：长流水线边跑边播，不让前端干等
        fn = progress_holder["fn"]
        if fn is not None:
            fn(msg)

    from src.agents.project import load_project_instructions
    tools = (build_read_tools(cwd) + build_research_tools(cwd) + build_web_tools()
             + build_artifact_tools(cwd) + build_dev_tools(cwd, on_progress=_progress)
             + build_command_tool(cwd, _confirm) + build_pr_tool(cwd, _confirm))
    extra = load_project_instructions(cwd) or None  # AGENTS.md/CLAUDE.md 项目约定进系统提示
    agent = MainAgent(tools, plan_tool=True, extra_system=extra)  # 网页主 agent：持久计划 + 隔离 dev + 受 WS 确认的 shell
    agent._web_confirm_holder = confirm_holder     # _run_agent_turn 每回合把它指向当前 ws
    agent._web_progress_holder = progress_holder
    return agent


def _get_session(websocket) -> Dict[str, Any]:
    """取/建该会话状态（agent + 展示 transcript + 活动时间）；超额淘汰最久未活动的。

    内存里没有时，先尝试从磁盘按 sid 复原（跨服务器重启）——还原 transcript + agent 历史/计划。
    """
    import os
    import time
    key = _session_key(websocket)
    sess = _SESSIONS.get(key)
    if sess is None:
        if len(_SESSIONS) >= _MAX_SESSIONS:
            oldest = min(_SESSIONS, key=lambda k: _SESSIONS[k]["last"])
            _SESSIONS.pop(oldest, None)
        agent = _new_agent()
        transcript: list = []
        try:                                  # 跨重启复原：磁盘有这个 sid 就把历史/计划灌回 agent
            from src.web.session_store import load_session
            saved = load_session(os.getcwd(), key)
        except Exception:  # noqa: BLE001
            saved = None
        if saved:
            transcript = list(saved.get("transcript") or [])
            agent.history = list(saved.get("history") or [])
            if saved.get("plan"):
                agent.plan = list(saved["plan"])
        sess = {"agent": agent, "transcript": transcript, "last": 0.0}
        _SESSIONS[key] = sess
    sess["last"] = time.monotonic()
    return sess


def _persist_session(websocket) -> None:
    """把当前会话存盘（跨重启用）。失败安全吞掉，绝不影响对话。"""
    import os
    sess = _SESSIONS.get(_session_key(websocket))
    if not sess:
        return
    try:
        from src.web.session_store import save_session
        agent = sess.get("agent")
        save_session(os.getcwd(), _session_key(websocket), sess.get("transcript") or [],
                     getattr(agent, "history", []) or [], getattr(agent, "plan", None))
    except Exception:  # noqa: BLE001
        pass


def _ws_agent(websocket):
    return _get_session(websocket)["agent"]


def _record(websocket, role: str, text: str) -> None:
    """把一条用户输入/最终回复记进展示 transcript，供重连回放（截断防膨胀）。"""
    sess = _SESSIONS.get(_session_key(websocket))
    if sess is None:
        return
    t = sess["transcript"]
    t.append({"role": role, "text": str(text)})
    if len(t) > _MAX_TRANSCRIPT:
        del t[:-_MAX_TRANSCRIPT]


async def _replay_history(websocket) -> None:
    """重连/刷新时把这个会话之前的对话回放给前端（init 之后调用）。"""
    sess = _SESSIONS.get(_session_key(websocket))
    if not sess:
        return
    if sess["transcript"]:
        await websocket.send_json({"type": "agent_history", "items": sess["transcript"]})
    plan = getattr(sess["agent"], "plan", None)        # 重连也恢复当前计划面板
    if plan:
        await websocket.send_json({"type": "agent_plan", "items": plan})


# 每个会话同时只跑一个回合；记下任务，供「停止」(agent_cancel)与断开时取消
_WS_AGENT_TASKS: Dict[str, Any] = {}


def _cancel_agent_turn(websocket) -> bool:
    """取消该会话正在跑的回合（若有）。返回是否真的发起了取消。"""
    task = _WS_AGENT_TASKS.get(_session_key(websocket))
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
    images = _sanitize_images(message.get("images"))       # 多模态：前端传来的 data 图
    audio = _sanitize_audio(message.get("audio"))          # 多模态：前端传来的 data 音频
    if not text and not images and not audio:
        await websocket.send_json({"type": "agent_error", "text": "空输入"})
        return
    if not os.getenv("OPENAI_API_KEY"):
        await websocket.send_json({"type": "agent_emit", "text": "未配置 OPENAI_API_KEY，无法对话。"})
        await websocket.send_json({"type": "agent_done"})
        return

    key = _session_key(websocket)
    existing = _WS_AGENT_TASKS.get(key)
    if existing is not None and not existing.done():       # 不并发：上一条还在跑就提示
        await websocket.send_json({"type": "agent_error", "text": "上一条还在跑，先等它结束或点「停止」。"})
        return

    if not text and (images or audio):                     # 纯附件轮：给个温和的默认指令
        text = "请听这段音频并转写/回答。" if audio and not images else "请看图并描述/分析其中内容。"
    mode = message.get("mode", "plan")
    _WS_AGENT_TASKS[key] = asyncio.create_task(
        _run_agent_turn(websocket, text, mode, images, audio))


# 单个附件上限 ~8MB（base64 后），整轮图/音各最多 6 个——挡住误传大文件撑爆 WS/上下文
_MAX_IMG_CHARS = 8 * 1024 * 1024
_MAX_IMAGES = 6


def _sanitize_images(raw) -> list:
    """校验前端传来的图片引用：只收 data:image/ 或 http(s) URL，限大小与张数。"""
    if not isinstance(raw, list):
        return []
    out: list = []
    for item in raw:
        ref = item if isinstance(item, str) else (item.get("url") if isinstance(item, dict) else None)
        if not isinstance(ref, str):
            continue
        ref = ref.strip()
        if not (ref.startswith("data:image/") or ref.startswith(("http://", "https://"))):
            continue
        if len(ref) > _MAX_IMG_CHARS:
            continue
        out.append(ref)
        if len(out) >= _MAX_IMAGES:
            break
    return out


def _sanitize_audio(raw) -> list:
    """校验前端传来的音频引用：只收 data:audio/（input_audio 要内联数据），限大小与个数。"""
    if not isinstance(raw, list):
        return []
    out: list = []
    for item in raw:
        ref = item if isinstance(item, str) else (item.get("url") if isinstance(item, dict) else None)
        if not isinstance(ref, str):
            continue
        ref = ref.strip()
        if not ref.startswith("data:audio/"):
            continue
        if len(ref) > _MAX_IMG_CHARS:
            continue
        out.append(ref)
        if len(out) >= _MAX_IMAGES:
            break
    return out


async def handle_tts_message(websocket, message: Dict[str, Any]):
    """「🔊 播放」：把指定文本合成成语音，base64 WAV 回前端播放（失败回 agent_tts_error）。"""
    import base64
    import os
    cid = message.get("id")
    text = str(message.get("text", "")).strip()
    if not text:
        return
    if not os.getenv("OPENAI_API_KEY"):
        await websocket.send_json({"type": "agent_tts_error", "id": cid, "text": "未配置 OPENAI_API_KEY"})
        return
    try:
        from src.llm.client import LLMClient
        wav = await LLMClient().tts(text)
        b64 = base64.b64encode(wav).decode("ascii")
        await websocket.send_json({"type": "agent_tts_audio", "id": cid,
                                   "data": f"data:audio/wav;base64,{b64}"})
    except Exception as e:  # noqa: BLE001
        await websocket.send_json({"type": "agent_tts_error", "id": cid, "text": str(e)[:200]})


async def _run_agent_turn(websocket, text: str, mode: str, images: Optional[list] = None,
                          audio: Optional[list] = None):
    """实际跑一个回合：run_turn 产出的事件经队列串行发回前端；整个任务可被取消（中断）。

    事件类型：agent_say(工具提示) / agent_stream(增量) / agent_emit(成段输出) /
    agent_error / agent_done / agent_cancelled。
    """
    import contextlib

    agent = _ws_agent(websocket)              # 确保会话存在
    marks = (([f"🖼×{len(images)}"] if images else []) +
             ([f"🎧×{len(audio)}"] if audio else []))      # 带附件轮在回放里标记
    rec = f"{text}　{' '.join(marks)}" if marks else text
    _record(websocket, "user", rec)           # 记进展示历史，供重连回放
    q: asyncio.Queue = asyncio.Queue()
    agent._on_plan = lambda plan: q.put_nowait({"type": "agent_plan", "items": plan})  # 计划更新 → 推前端
    holder = getattr(agent, "_web_confirm_holder", None)
    if holder is not None:                    # 把 run_command 等的确认门绑到当前连接
        holder["fn"] = _make_ws_confirm(websocket, q)

    def agent_say(m):
        try:                                  # 剥掉 Rich 标记，Web 端不显示 [b]/[dim] 等原文
            from rich.text import Text as _Rt
            m = _Rt.from_markup(str(m)).plain
        except Exception:  # noqa: BLE001
            pass
        q.put_nowait({"type": "agent_say", "text": m})

    ph = getattr(agent, "_web_progress_holder", None)
    if ph is not None:                        # dev 流水线进度（并行实现/修复/接力/集成验证）也走 agent_say
        ph["fn"] = agent_say

    def agent_emit(m):
        _record(websocket, "assistant", m)    # 最终回复进展示历史
        q.put_nowait({"type": "agent_emit", "text": m})

    def agent_stream(p):
        q.put_nowait({"type": "agent_stream", "text": p})

    async def _run():
        try:
            await agent.run_turn(text, mode=mode, say=agent_say, emit=agent_emit,
                                 stream_cb=agent_stream, images=images, audio=audio)
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
        key = _session_key(websocket)
        if _WS_AGENT_TASKS.get(key) is asyncio.current_task():
            _WS_AGENT_TASKS.pop(key, None)
        _persist_session(websocket)           # 回合收尾存盘（含中断）：跨服务器重启不丢对话
