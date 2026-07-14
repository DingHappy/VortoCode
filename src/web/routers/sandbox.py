"""sandbox 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

@router.post("/api/sandbox/execute")
async def execute_in_sandbox(request: SandboxExecuteRequest):
    """在本地沙箱中执行代码；与云沙箱同闸——需 shell capability。

    此前这条路由**既没有 require_shell() 也没有任何安全检查**，而它的兄弟
    （`/api/sandbox/{id}/execute`）两者都有：同为"执行代码"的入口，一个把着门、一个大敞。
    执行闸是本仓的硬规矩（CLAUDE.md：命令执行走 require_shell()），补齐。
    """
    require_shell()
    try:
        result = await state.sandbox_executor.execute_in_sandbox(
            request.code,
            request.language,
            request.timeout
        )
        
        return {
            "success": result.success,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# 云端沙箱 API
@router.post("/api/sandbox/create")
async def create_sandbox(template: str = "python", name: str = None):
    """创建云沙箱会话；执行仍受共享 sandbox policy，入口默认需 shell capability。"""
    require_shell()
    from src.cloud_sandbox import CloudSandboxManager

    # 修正：原本误判 sandbox_manager（恒存在）导致 cloud_sandbox_manager 永不初始化
    if not hasattr(state, 'cloud_sandbox_manager'):
        state.cloud_sandbox_manager = CloudSandboxManager()
    
    try:
        sandbox = await state.cloud_sandbox_manager.create_sandbox(template, name)
        return {"success": True, "sandbox": sandbox.get_stats()}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/api/sandbox/list")
async def list_sandboxes():
    """列出沙箱"""
    if not hasattr(state, 'cloud_sandbox_manager'):
        return {"sandboxes": []}
    
    sandboxes = await state.cloud_sandbox_manager.list_sandboxes()
    return {"sandboxes": sandboxes}

@router.post("/api/sandbox/{sandbox_id}/execute")
async def execute_in_sandbox(sandbox_id: str, command: str, language: str = "bash"):
    """在云沙箱 workspace 执行；需 shell capability，并严格遵守共享 sandbox policy。"""
    require_shell()
    guard = state.safety_guard.check_command(command)  # 权限模型命令安全检查 + 记录违规
    if not guard.get("allowed"):
        return {"success": False, "error": f"命令被安全策略拦截：{guard.get('reason')}"}
    if not hasattr(state, 'cloud_sandbox_manager'):
        return {"success": False, "error": "Sandbox manager not initialized"}
    
    result = await state.cloud_sandbox_manager.execute_in_sandbox(
        sandbox_id, command, language
    )
    return result.model_dump()

@router.post("/api/sandbox/{sandbox_id}/destroy")
async def destroy_sandbox(sandbox_id: str):
    """销毁沙箱"""
    if not hasattr(state, 'cloud_sandbox_manager'):
        return {"success": False, "error": "Sandbox manager not initialized"}
    
    success = await state.cloud_sandbox_manager.destroy_sandbox(sandbox_id)
    return {"success": success}
