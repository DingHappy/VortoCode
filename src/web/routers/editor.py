"""editor 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

@router.post("/api/editor/edit")
async def edit_code(request: EditRequest):
    """编辑代码"""
    # 路径确认在工作目录内，拒绝绝对路径/.. 越界写入
    if resolve_within(state.workdir, request.file) is None:
        return {"success": False, "error": "路径越界：只能编辑工作目录内的文件"}
    try:
        if not state.code_editor:
            state.code_editor = CodeEditor(state.workdir)

        diff = state.code_editor.edit_file(
            request.file,
            request.line,
            request.end_line,
            request.content
        )
        
        return {
            "success": True,
            "diff": {
                "file": diff.file,
                "additions": diff.additions,
                "deletions": diff.deletions,
                "content": DiffGenerator.generate_diff(
                    diff.old_content, diff.new_content, diff.file
                )
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/editor/apply")
async def apply_edits():
    """应用编辑"""
    if not state.code_editor:
        return {"success": False, "error": "No editor initialized"}
    
    try:
        state.code_editor.apply_changes(auto_save=True)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/editor/undo")
async def undo_edit():
    """撤销编辑"""
    if not state.code_editor:
        return {"success": False, "error": "No editor initialized"}
    
    try:
        diffs = state.code_editor.undo()
        return {"success": True, "diffs": diffs}
    except Exception as e:
        return {"success": False, "error": str(e)}
