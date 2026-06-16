"""system 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/status")
async def get_status():
    """获取系统状态"""
    return state.to_dict()

@router.get("/api/workdir")
async def get_workdir():
    """获取当前工作目录"""
    return {"workdir": state.workdir}

@router.post("/api/workdir")
async def set_workdir(request: WorkdirRequest):
    """设置工作目录"""
    workdir = request.workdir
    
    # 展开 ~ 符号
    if workdir.startswith("~"):
        workdir = str(Path.home() / workdir[2:])
    
    # 检查目录是否存在
    workdir_path = Path(workdir)
    if not workdir_path.exists():
        try:
            # 尝试创建目录
            workdir_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"success": False, "error": f"无法创建目录: {e}"}
    
    if not workdir_path.is_dir():
        return {"success": False, "error": "路径不是目录"}
    
    state.workdir = str(workdir_path.resolve())
    
    await manager.broadcast({
        "type": "workdir_changed",
        "data": {"workdir": state.workdir}
    })
    
    return {"success": True, "workdir": state.workdir}

@router.get("/api/models")
async def get_models():
    """获取可用模型列表"""
    return {
        "models": AVAILABLE_MODELS,
        "current": state.model
    }

@router.post("/api/model")
async def set_model(request: ModelRequest):
    """设置模型"""
    # 检查模型是否可用
    model_ids = [m["id"] for m in AVAILABLE_MODELS]
    if request.model not in model_ids:
        return {"success": False, "error": "不支持的模型"}
    
    state.model = request.model
    
    await manager.broadcast({
        "type": "model_changed",
        "data": {"model": state.model}
    })
    
    return {"success": True, "model": state.model}

@router.get("/api/files")
async def list_files(path: str = ""):
    """列出工作目录下的文件"""
    base_dir = Path(state.workdir).resolve()
    target_dir = resolve_within(state.workdir, path or ".")
    if target_dir is None:
        return {"success": False, "error": "访问被拒绝"}

    if not target_dir.exists():
        return {"success": False, "error": "目录不存在"}
    
    files = []
    try:
        for item in sorted(target_dir.iterdir()):
            # 跳过隐藏文件和常见忽略目录
            if item.name.startswith('.') or item.name in ['node_modules', '__pycache__', 'venv']:
                continue
            
            files.append({
                "name": item.name,
                "path": str(item.relative_to(base_dir)),
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else 0,
                "modified": item.stat().st_mtime
            })
    except PermissionError:
        return {"success": False, "error": "权限不足"}
    
    return {"success": True, "files": files}

# 文件浏览器 API
@router.get("/api/files")
async def list_files(path: str = ""):
    """列出文件"""
    try:
        base_dir = Path(state.workdir).resolve()
        target_dir = resolve_within(state.workdir, path or ".")
        if target_dir is None:
            return {"success": False, "error": "Access denied"}

        if not target_dir.exists():
            return {"success": False, "error": "Directory not found"}

        files = []
        ignore_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build'}

        for item in sorted(target_dir.iterdir()):
            if item.name.startswith('.'):  # 不暴露任何隐藏文件（含 .env/.gitignore）
                continue
            if any(d in item.parts for d in ignore_dirs):
                continue
            
            files.append({
                "name": item.name,
                "path": str(item.relative_to(base_dir)),
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else 0,
                "extension": item.suffix if item.is_file() else "",
                "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat()
            })
        
        return {"success": True, "files": files, "path": str(target_dir.relative_to(base_dir))}
    except Exception as e:
        return {"success": False, "error": str(e)}

# 代码预览 API
@router.get("/api/files/content")
async def get_file_content(path: str):
    """获取文件内容"""
    try:
        file_path = resolve_within(state.workdir, path)
        if file_path is None:
            return {"success": False, "error": "Access denied"}

        if not file_path.exists():
            return {"success": False, "error": "File not found"}
        
        if not file_path.is_file():
            return {"success": False, "error": "Not a file"}
        
        # 检查文件大小
        if file_path.stat().st_size > 1024 * 1024:  # 1MB
            return {"success": False, "error": "File too large"}
        
        content = file_path.read_text(encoding='utf-8')
        
        return {
            "success": True,
            "content": content,
            "path": path,
            "size": file_path.stat().st_size,
            "extension": file_path.suffix
        }
    except UnicodeDecodeError:
        return {"success": False, "error": "Binary file"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/terminal/execute")
async def execute_terminal(request: TerminalRequest):
    """执行终端命令（默认禁用，需 AUTODEV_ENABLE_SHELL=1）"""
    require_shell()  # fail-closed：必须在 try 之外，否则 403 会被吞成 200
    import asyncio

    workdir = request.workdir or state.workdir

    # 安全检查：经权限模型的 SafetyGuard（黑名单/危险模式 + 记录违规），
    # 替换原先的弱内联黑名单。被拦截的命令进 violation_history，/api/security 可见。
    guard = state.safety_guard.check_command(request.command)
    if not guard.get("allowed"):
        return {"success": False, "error": f"命令被安全策略拦截：{guard.get('reason')}"}

    try:
        # 执行命令
        process = await asyncio.create_subprocess_shell(
            request.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=30
            )
        except asyncio.TimeoutError:
            process.kill()
            return {"success": False, "error": "Command timed out"}
        
        return {
            "success": process.returncode == 0,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "return_code": process.returncode
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# 代码搜索 API
@router.get("/api/search")
async def search_code(query: str, file_type: str = None):
    """搜索代码"""
    try:
        results = []
        base_dir = Path(state.workdir)
        
        ignore_dirs = {'.git', 'node_modules', '__pycache__', 'venv'}
        search_extensions = {'.py', '.js', '.ts', '.tsx', '.jsx', '.md', '.txt', '.json', '.yaml', '.yml'}
        
        if file_type:
            search_extensions = {f'.{file_type}'}
        
        file_count = 0
        for file_path in base_dir.rglob("*"):
            # 跳过忽略的目录
            if any(d in file_path.parts for d in ignore_dirs):
                continue
            
            if not file_path.is_file():
                continue
            
            if file_path.suffix not in search_extensions:
                continue
            
            try:
                content = file_path.read_text(encoding='utf-8')
                if query.lower() in content.lower():
                    # 找到匹配的行
                    for i, line in enumerate(content.split('\n'), 1):
                        if query.lower() in line.lower():
                            results.append({
                                "file": str(file_path.relative_to(base_dir)),
                                "line": i,
                                "text": line.strip()[:200],
                                "type": "content"
                            })
                            break
                
                # 文件名匹配
                if query.lower() in file_path.name.lower():
                    results.append({
                        "file": str(file_path.relative_to(base_dir)),
                        "line": 0,
                        "text": file_path.name,
                        "type": "filename"
                    })
            
            except (UnicodeDecodeError, PermissionError):
                # 跳过无法读取的文件
                pass
            except Exception as e:
                logger.debug(f"Error searching file {file_path}: {e}")
            
            file_count += 1
            if file_count >= 500:  # 限制搜索文件数
                break
        
        return {"success": True, "results": results[:50]}
    except Exception as e:
        return {"success": False, "error": str(e)}

# 系统信息 API
@router.get("/api/system/info")
async def get_system_info():
    """获取系统信息"""
    import platform
    import psutil
    
    try:
        return {
            "platform": platform.system(),
            "platform_version": platform.version(),
            "python_version": platform.python_version(),
            "cpu_count": psutil.cpu_count(),
            "memory_total": psutil.virtual_memory().total,
            "memory_available": psutil.virtual_memory().available,
            "disk_usage": psutil.disk_usage('/').percent,
            "workdir": state.workdir,
            "model": state.model,
            "version": "0.1.0"
        }
    except Exception:
        return {
            "platform": platform.system(),
            "python_version": platform.python_version(),
            "workdir": state.workdir,
            "model": state.model,
            "version": "0.1.0"
        }

# 模型路由 API
@router.get("/api/models/available")
async def get_available_models():
    """获取可用模型"""
    from src.models import ModelRouter
    
    if not hasattr(state, 'model_router'):
        state.model_router = ModelRouter()
    
    models = [
        {
            "id": m.id,
            "name": m.name,
            "provider": m.provider,
            "tier": m.tier.value,
            "cost_per_1k_input": m.cost_per_1k_input,
            "cost_per_1k_output": m.cost_per_1k_output,
            "quality": m.quality_rating,
            "speed": m.speed_rating
        }
        for m in state.model_router.models.values()
    ]
    
    return {"models": models}

@router.get("/api/models/recommend")
async def recommend_model(task: str):
    """推荐模型"""
    from src.models import ModelRouter
    
    if not hasattr(state, 'model_router'):
        state.model_router = ModelRouter()
    
    recommendations = state.model_router.get_recommendations(task)
    return {"recommendations": recommendations}

@router.get("/api/cost/report")
async def get_cost_report(period: str = "daily"):
    """获取成本报告（进程级全局 cost_tracker：LLM 调用实时埋点）+ 预算告警"""
    from src.models import cost_tracker
    return {**cost_tracker.get_report(period), "alerts": cost_tracker.alerts}
