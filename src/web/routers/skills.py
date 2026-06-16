"""skills 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# 技能管理 API
@router.get("/api/skills")
async def list_skills(category: str = None):
    """列出所有技能"""
    skills = state.skill_manager.list_skills(category)
    return {
        "skills": [skill.to_dict() for skill in skills]
    }

@router.get("/api/skills/{skill_name}")
async def get_skill(skill_name: str):
    """获取技能详情"""
    skill = state.skill_manager.get_skill(skill_name)
    if skill:
        return {"success": True, "skill": skill.to_dict()}
    return {"success": False, "error": "Skill not found"}

@router.get("/api/skills/search/{query}")
async def search_skills(query: str):
    """搜索技能"""
    skills = state.skill_manager.search_skills(query)
    return {
        "skills": [skill.to_dict() for skill in skills]
    }

@router.post("/api/skills/execute")
async def execute_skill(request: ExecuteSkillRequest):
    """执行技能"""
    try:
        result = state.skill_manager.execute_skill(
            request.skill_name,
            request.arguments
        )
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/skills")
async def create_skill(request: CreateSkillRequest):
    """创建新技能"""
    try:
        skill = state.skill_manager.create_skill(
            name=request.name,
            description=request.description,
            content=request.content,
            category=request.category,
            capabilities=request.capabilities,
            tools=request.tools
        )
        return {
            "success": True,
            "skill": skill.to_dict()
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/skills/reload")
async def reload_skills():
    """重新加载技能"""
    state.skill_manager.registry.reload()
    return {"success": True}
