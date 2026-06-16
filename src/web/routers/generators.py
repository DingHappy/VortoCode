"""generators 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# 模板 API
@router.get("/api/templates")
async def list_templates(category: str = None):
    """列出模板"""
    from src.templates import TemplateManager
    
    if not hasattr(state, 'template_manager'):
        state.template_manager = TemplateManager()
    
    templates = state.template_manager.list_templates(category)
    return {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "category": t.category,
                "tech_stack": t.tech_stack,
                "icon": t.icon,
                "variables": [v.model_dump() for v in t.variables]
            }
            for t in templates
        ]
    }

@router.get("/api/templates/{template_id}")
async def get_template(template_id: str):
    """获取模板详情"""
    from src.templates import TemplateManager
    
    if not hasattr(state, 'template_manager'):
        state.template_manager = TemplateManager()
    
    template = state.template_manager.get_template(template_id)
    if template:
        return {
            "success": True,
            "template": {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "category": template.category,
                "tech_stack": template.tech_stack,
                "variables": [v.model_dump() for v in template.variables],
                "files": [{"path": f.path, "content": f.content[:200]} for f in template.files],
                "instructions": template.instructions
            }
        }
    return {"success": False, "error": "Template not found"}

@router.post("/api/templates/{template_id}/create")
async def create_from_template(template_id: str, output_dir: str, variables: Dict[str, str] = None):
    """从模板创建项目"""
    from src.templates import TemplateManager
    
    if not hasattr(state, 'template_manager'):
        state.template_manager = TemplateManager()
    
    try:
        files = state.template_manager.create_project(template_id, output_dir, variables)
        return {"success": True, "files": files}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/testing/generate")
async def generate_tests(request: TestGenerateRequest):
    """生成测试代码（有 OPENAI_API_KEY 时 LLM 生成真测试，否则确定性骨架）"""
    from src.testing import TestGenerator

    generator = TestGenerator()
    tests = await generator.agenerate_tests(request.code, request.language, request.test_type)
    return {"success": True, "tests": tests}

@router.post("/api/docs/generate")
async def generate_docs(request: DocGenerateRequest):
    """生成文档（有 OPENAI_API_KEY 时 LLM 增强，否则确定性 AST 文档）"""
    from src.documentation import DocGenerator

    generator = DocGenerator()
    docs = await generator.agenerate_docs(request.code, request.language)
    return {"success": True, "docs": docs}
