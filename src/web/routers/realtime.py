"""realtime 路由（从 server.py 拆出）。

/agent 的 WS 协议面已冻结在 src/gateway/protocol.py（D1 单核化 PR-1）：事件一律经
P.make_event 构造/P.parse_event 校验，新事件类型必须先在 protocol 登记（契约测试守着）。
"""
from fastapi import Request

from src.gateway import protocol as P
from src.gateway.sessions import SessionTable
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
        # 发送当前状态（v=协议版本随握手下发）
        await websocket.send_json(P.make_event(P.INIT, v=P.PROTOCOL_VERSION, data=state.to_dict()))
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
    """处理 WebSocket 消息（协议面见 gateway/protocol.py；不合法消息按旧行为静默忽略）"""
    try:
        msg_type, message = P.parse_event(message)
    except P.ProtocolError as e:              # 未登记类型/缺必填：与从前"未知类型掉落"同效，只记日志
        logger.debug(f"忽略不合协议的 WS 消息: {e}")
        return

    if msg_type == P.PING:
        await websocket.send_json(P.make_event(P.PONG))

    elif msg_type == P.GET_STATUS:
        await websocket.send_json(P.make_event(P.STATUS, data=state.to_dict()))

    elif msg_type == P.AGENT:
        await handle_agent_message(websocket, message)

    elif msg_type == P.AGENT_CANCEL:          # 「停止」：中断正在跑的回合（若有）
        _cancel_agent_turn(websocket)

    elif msg_type == P.AGENT_CONFIRM_RESPONSE:  # 前端对 agent_confirm 的应答 → 解开等待的工具
        fut = _PENDING_CONFIRMS.get(message.get("id"))
        if fut is not None and not fut.done():
            fut.set_result(bool(message.get("ok")))

    elif msg_type == P.AGENT_TTS:             # 「🔊 播放」：把某条回复合成成语音回传前端播放
        await handle_tts_message(websocket, message)

    elif msg_type == P.TASK_LIST:             # 后台任务快照（客户端连上/刷新时 hydrate 任务列表）
        from src.web.routers.tasks import get_runner
        await websocket.send_json(P.make_event(
            P.TASK_SNAPSHOT, data=[t.to_dict() for t in get_runner().list()]))


def broadcast_task_update(task: dict) -> None:
    """把一条后台任务状态变更广播给所有连着的 WS 客户端（best-effort；无循环/无连接则静默）。

    由 gateway.TaskRunner 的 on_update 回调（同步）调用——这里把异步 broadcast 调度到事件循环上。
    """
    payload = P.make_event(P.TASK_UPDATE, data=task)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:                       # 没有运行中的事件循环（如同步测试路径）→ 静默跳过
        return
    loop.create_task(manager.broadcast(payload))


def broadcast_notice(text: str) -> None:
    """把一条后台通知（cron 结果 / heartbeat 发现）广播给所有连着的 WS 客户端（best-effort）。

    daemon 路径的投递终点之一（另一个是持久台账 GET /api/notices），见 tasks.scheduler_loop._notify。
    """
    payload = P.make_event(P.NOTICE, data={"text": str(text)[:2000]})
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(manager.broadcast(payload))


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
        q.put_nowait(P.make_event(P.AGENT_CONFIRM, id=cid, text=str(message)))
        try:
            return bool(await asyncio.wait_for(fut, timeout=300))
        except Exception:  # noqa: BLE001  # 超时/取消 → 拒绝（安全）
            return False
        finally:
            _PENDING_CONFIRMS.pop(cid, None)
    return _confirm


# ---- 会话级持久化：按客户端 sid 让 agent 跨重连/刷新存活，另留一份干净的展示用 transcript ----
# 以前 agent 按 id(websocket) 存活、断开即丢，刷新就重来。现在按 sid（前端 sessionStorage，
# 刷新仍在）存活，重连后回放对话。生命周期管理在 gateway.SessionTable（上限淘汰/磁盘复原/持久），
# 这里只持有表 + WS 侧的键规范；_SESSIONS 是表底层 dict 的别名（回放/删除/测试直接操作同一对象）。
_TABLE = SessionTable(on_evict=lambda sess: _shutdown_mcp_async(sess.get("agent")))
_SESSIONS: Dict[str, Dict[str, Any]] = _TABLE.data
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
    """Web 端薄壳：装配走 gateway 的单一工厂（kind="web"，含制品工具）；端侧只留 holder 模式——
    confirm/progress 每回合重绑到当前连接（见 _run_agent_turn），无回合上下文时 confirm 默认拒绝。"""
    import os
    from src.gateway.agent_session import build_session
    confirm_holder = {"fn": None}
    progress_holder = {"fn": None}                 # dev 流水线进度 → 每回合重绑到当前 ws 的 agent_say

    async def _confirm(message: str) -> bool:
        fn = confirm_holder["fn"]
        return bool(await fn(message)) if fn is not None else False

    def _progress(msg: str) -> None:               # 同步、best-effort：长流水线边跑边播，不让前端干等
        fn = progress_holder["fn"]
        if fn is not None:
            fn(msg)

    agent = build_session(os.getcwd(), kind="web", confirm=_confirm, on_progress=_progress)
    agent._web_confirm_holder = confirm_holder     # _run_agent_turn 每回合把它指向当前 ws
    agent._web_progress_holder = progress_holder
    return agent


async def _ensure_mcp(agent, say) -> None:
    """网页会话首回合：**opt-in** 连 MCP（env VORTOCODE_WEB_MCP=1 才连）、把工具接入本会话 agent（只试一次）。

    默认不连——网页无 /mcp 这类显式入口，若每回合自动连，一个慢/坏的 server 会拖死每回合；
    故与 TUI(/mcp)、CLI(--mcp) 一样走显式 opt-in。开了之后无配置 → connect_mcp 返 (None, [])、
    静默跳过；失败不影响回合。manager 挂 agent._mcp_mgr，会话淘汰时 _shutdown_mcp_async 关掉。
    """
    if getattr(agent, "_mcp_tried", False):
        return
    agent._mcp_tried = True
    agent._mcp_mgr = None
    import os
    if os.getenv("VORTOCODE_WEB_MCP") not in ("1", "true", "yes"):
        return                              # 默认关闭（避免坏配置卡死每回合）
    try:
        from src.agents.mcp_tools import connect_mcp
        mgr, mcp_tools = await connect_mcp(os.getcwd())
        if mcp_tools:
            agent.add_tools(mcp_tools)
            agent._mcp_mgr = mgr
            say(f"🔌 接入 {len(mcp_tools)} 个 MCP 工具")
    except Exception as e:  # noqa: BLE001
        say(f"🔌 MCP 连接失败: {e}")


def _shutdown_mcp_async(agent) -> None:
    """会话淘汰时异步关掉其 MCP manager（fire-and-forget；没有运行中的 loop 就跳过）。"""
    mgr = getattr(agent, "_mcp_mgr", None)
    if mgr is None:
        return
    try:
        asyncio.create_task(mgr.shutdown())
    except RuntimeError:           # 没有运行中的事件循环
        pass


def _get_session(websocket) -> Dict[str, Any]:
    """取/建该会话状态（agent + 展示 transcript + 活动时间）——生命周期全在 SessionTable。

    factory/_MAX_SESSIONS 按当前模块属性取（测试可 monkeypatch）；淘汰时表回调关 MCP。
    """
    import os
    return _TABLE.get(_session_key(websocket), repo_root=os.getcwd(),
                      factory=_new_agent, max_sessions=_MAX_SESSIONS)


def _persist_session(websocket) -> None:
    """把当前会话存盘（跨重启用）。失败安全吞掉，绝不影响对话。"""
    import os
    _TABLE.persist(_session_key(websocket), os.getcwd())


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


# ---- 多会话管理 API（供 agent.html 的会话侧栏：列表/删除/重命名；新建=前端换 sid 隐式创建）----

@router.get("/api/agent/sessions")
async def agent_sessions_list():
    """列出所有已落盘会话（{sid,title,messages,updated}，最近更新在前）。"""
    import os
    from src.web.session_store import list_sessions
    return {"sessions": list_sessions(os.getcwd())}


@router.delete("/api/agent/sessions/{sid}")
async def agent_sessions_delete(sid: str):
    """删除一个会话：磁盘落盘 + 顺手逐出内存 _SESSIONS（并关其 MCP，别残留子进程）。"""
    import os
    from src.web.session_store import delete_session
    on_disk = delete_session(os.getcwd(), sid)
    sess = _SESSIONS.pop(f"sid-{sid}", None)
    if sess:
        _shutdown_mcp_async(sess.get("agent"))
    return {"deleted": bool(on_disk or sess)}


@router.patch("/api/agent/sessions/{sid}")
async def agent_sessions_rename(sid: str, request: Request):
    """重命名一个会话（body: {"title": "..."}）。"""
    import os
    from src.web.session_store import rename_session
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    title = str((body or {}).get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title required"}
    return {"ok": rename_session(os.getcwd(), sid, title)}


async def _replay_history(websocket) -> None:
    """重连/刷新时把这个会话之前的对话回放给前端（init 之后调用）。"""
    sess = _SESSIONS.get(_session_key(websocket))
    if not sess:
        return
    if sess["transcript"]:
        await websocket.send_json(P.make_event(P.AGENT_HISTORY, items=sess["transcript"]))
    plan = getattr(sess["agent"], "plan", None)        # 重连也恢复当前计划面板
    if plan:
        await websocket.send_json(P.make_event(P.AGENT_PLAN, items=plan))


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
    rid = message.get("rid")                               # 可选 request id：本回合所有出站事件回带
    rid = str(rid)[:64] if rid is not None else None
    if not text and not images and not audio:
        await websocket.send_json(P.make_event(P.AGENT_ERROR, text="空输入", rid=rid))
        return
    if not os.getenv("OPENAI_API_KEY"):
        await websocket.send_json(P.make_event(
            P.AGENT_EMIT, text="未配置 OPENAI_API_KEY，无法对话。", rid=rid))
        await websocket.send_json(P.make_event(P.AGENT_DONE, rid=rid))
        return

    key = _session_key(websocket)
    existing = _WS_AGENT_TASKS.get(key)
    if existing is not None and not existing.done():       # 不并发：上一条还在跑就提示
        await websocket.send_json(P.make_event(
            P.AGENT_ERROR, text="上一条还在跑，先等它结束或点「停止」。", rid=rid))
        return

    if not text and (images or audio):                     # 纯附件轮：给个温和的默认指令
        text = "请听这段音频并转写/回答。" if audio and not images else "请看图并描述/分析其中内容。"
    mode = message.get("mode", "plan")
    _WS_AGENT_TASKS[key] = asyncio.create_task(
        _run_agent_turn(websocket, text, mode, images, audio, rid=rid))


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
        await websocket.send_json(P.make_event(P.AGENT_TTS_ERROR, id=cid, text="未配置 OPENAI_API_KEY"))
        return
    try:
        from src.llm.client import LLMClient
        wav = await LLMClient().tts(text)
        b64 = base64.b64encode(wav).decode("ascii")
        await websocket.send_json(P.make_event(
            P.AGENT_TTS_AUDIO, id=cid, data=f"data:audio/wav;base64,{b64}"))
    except Exception as e:  # noqa: BLE001
        await websocket.send_json(P.make_event(P.AGENT_TTS_ERROR, id=cid, text=str(e)[:200]))


async def _run_agent_turn(websocket, text: str, mode: str, images: Optional[list] = None,
                          audio: Optional[list] = None, rid: Optional[str] = None):
    """实际跑一个回合：run_turn 产出的事件经队列串行发回前端；整个任务可被取消（中断）。

    事件类型：agent_say(工具提示) / agent_stream(增量) / agent_emit(成段输出) /
    agent_error / agent_done / agent_cancelled。rid 非空时本回合**所有**出站事件都回带它
    （统一在 drain 循环注入，单点覆盖队列里的全部事件）。
    """
    import contextlib

    agent = _ws_agent(websocket)              # 确保会话存在
    marks = (([f"🖼×{len(images)}"] if images else []) +
             ([f"🎧×{len(audio)}"] if audio else []))      # 带附件轮在回放里标记
    rec = f"{text}　{' '.join(marks)}" if marks else text
    _record(websocket, "user", rec)           # 记进展示历史，供重连回放
    q: asyncio.Queue = asyncio.Queue()
    agent._on_plan = lambda plan: q.put_nowait(P.make_event(P.AGENT_PLAN, items=plan))  # 计划更新 → 推前端
    holder = getattr(agent, "_web_confirm_holder", None)
    if holder is not None:                    # 把 run_command 等的确认门绑到当前连接
        holder["fn"] = _make_ws_confirm(websocket, q)

    def agent_say(m):
        try:                                  # 剥掉 Rich 标记，Web 端不显示 [b]/[dim] 等原文
            from rich.text import Text as _Rt
            m = _Rt.from_markup(str(m)).plain
        except Exception:  # noqa: BLE001
            pass
        q.put_nowait(P.make_event(P.AGENT_SAY, text=m))

    ph = getattr(agent, "_web_progress_holder", None)
    if ph is not None:                        # dev 流水线进度（并行实现/修复/接力/集成验证）也走 agent_say
        ph["fn"] = agent_say

    def agent_emit(m):
        _record(websocket, "assistant", m)    # 最终回复进展示历史
        q.put_nowait(P.make_event(P.AGENT_EMIT, text=m))

    def agent_stream(p):
        q.put_nowait(P.make_event(P.AGENT_STREAM, text=p))

    async def _run():
        try:
            sess = _SESSIONS.get(_session_key(websocket))   # 本会话独立用量作用域（多会话互不串扰）
            if sess and sess.get("usage") is not None:
                from src.llm.client import bind_usage
                bind_usage(sess["usage"])
            await _ensure_mcp(agent, agent_say)   # 首回合按需连 MCP（config/mcp.yaml 存在才连）
            await agent.run_turn(text, mode=mode, say=agent_say, emit=agent_emit,
                                 stream_cb=agent_stream, images=images, audio=audio)
        except asyncio.CancelledError:        # 中断：直接上抛，不当成错误
            raise
        except Exception as e:  # noqa: BLE001
            q.put_nowait(P.make_event(P.AGENT_ERROR, text=str(e)))
        finally:
            q.put_nowait(None)

    inner = asyncio.create_task(_run())
    try:
        while True:
            evt = await q.get()
            if evt is None:
                break
            if rid is not None:               # 单点注入：本回合队列里的全部事件都回带 rid
                evt.setdefault("rid", rid)
            await websocket.send_json(evt)
        await inner
        await websocket.send_json(P.make_event(P.AGENT_DONE, rid=rid))
    except asyncio.CancelledError:            # 收到「停止」：连同在飞的 LLM 调用一起取消
        inner.cancel()
        # 注意 CancelledError 是 BaseException，suppress(Exception) 抓不到，必须显式列出
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await inner
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await websocket.send_json(P.make_event(P.AGENT_CANCELLED, text="已中断", rid=rid))
        # 故意吞掉 CancelledError：这是一次性后台任务，优雅收尾即可
    finally:
        key = _session_key(websocket)
        if _WS_AGENT_TASKS.get(key) is asyncio.current_task():
            _WS_AGENT_TASKS.pop(key, None)
        _persist_session(websocket)           # 回合收尾存盘（含中断）：跨服务器重启不丢对话
