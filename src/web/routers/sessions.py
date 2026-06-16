"""sessions 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# 会话管理 API
@router.post("/api/sessions")
async def create_session(name: str = None):
    """创建会话"""
    from src.memory import SessionManager
    if not hasattr(state, 'session_manager'):
        state.session_manager = SessionManager()
    
    session_id = state.session_manager.start_session(name)
    return {"success": True, "session_id": session_id}

@router.get("/api/sessions")
async def list_sessions():
    """列出会话"""
    from src.memory import SessionManager
    if not hasattr(state, 'session_manager'):
        state.session_manager = SessionManager()
    
    sessions = state.session_manager.list_recent_sessions()
    return {"sessions": sessions}

@router.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """获取会话消息"""
    from src.memory import SessionManager
    if not hasattr(state, 'session_manager'):
        state.session_manager = SessionManager()
    
    state.session_manager.resume_session(session_id)
    messages = state.session_manager.get_messages()
    return {"messages": messages}

@router.post("/api/completion")
async def get_completion(request: CompletionRequest):
    """获取代码补全"""
    from src.editor import CompletionEngine, CompletionContext
    
    engine = CompletionEngine(state.workdir)
    
    # 解析上下文
    lines = request.content.split('\n')
    lines_before = lines[max(0, request.line - 10):request.line]
    lines_after = lines[request.line + 1:min(len(lines), request.line + 10)]
    current_line = lines[request.line] if request.line < len(lines) else ""
    
    context = CompletionContext(
        file=request.file,
        line=request.line,
        column=request.column,
        prefix=current_line[:request.column],
        suffix=current_line[request.column:],
        language=request.language,
        lines_before=lines_before,
        lines_after=lines_after
    )
    
    completions = await engine.get_completions(context)
    
    return {
        "success": True,
        "completions": [c.to_dict() for c in completions]
    }

@router.get("/api/chat/history")
async def get_chat_history(session_id: str = None, limit: int = 50):
    """获取对话历史"""
    if not session_id:
        # 返回当前会话
        session_id = state.session_manager.current_session_id if hasattr(state, 'session_manager') else None
    
    if not session_id:
        return {"messages": []}
    
    try:
        messages = state.session_manager.store.get_messages(session_id, limit)
        return {"messages": messages}
    except Exception as e:
        return {"messages": [], "error": str(e)}

@router.post("/api/chat/send")
async def send_chat_message(message: ChatMessage):
    """发送消息"""
    try:
        # 确保有会话
        if not hasattr(state, 'session_manager'):
            from src.memory import SessionManager
            state.session_manager = SessionManager()
        
        if not state.session_manager.current_session_id:
            state.session_manager.start_session()
        
        # 保存消息
        state.session_manager.add_message(message.role, message.content)
        
        # 广播消息
        await manager.broadcast({
            "type": "chat_message",
            "data": {
                "role": message.role,
                "content": message.content,
                "timestamp": message.timestamp.isoformat()
            }
        })
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
