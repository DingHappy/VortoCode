"""General-only instruction loading; no save/enable tool exposed to the model."""
from src.agents.tool import Tool
from src.workflow.workbench.personal_skills import PersonalSkills


def build_personal_skill_tools(root: str) -> list[Tool]:
    skills = PersonalSkills(root)

    async def listing(args):
        try:
            snapshot = skills.list()
            return {**snapshot, "detail": "仅 enabled=true 且 model_invocable=true 的技能可加载；权限仍按当前会话确认。"}
        except Exception:
            return {"ok": False, "error": "个人技能清单无法读取，请核对助理技能管理页。"}

    async def use(args):
        try:
            item = skills.load_instructions(args.get("id", ""))
        except Exception:
            return {"ok": False, "error": "个人技能不可加载，请检查启用状态与审阅版本。"}
        return {"ok": True, "stage": "instructions_loaded", "id": item["id"], "revision": item["revision"],
                "instructions": item["instructions"],
                "detail": "仅加载了已审阅版本的指令，不代表任务完成。继续使用现有工具；写入、执行、外发仍须原授权。"
                          "不执行技能 Hook 或脚本，不自动读取相对引用；需要文件时由用户明确指定并授权。"}

    return [Tool("list_personal_skills", "查看私人助理的个人技能及当前启用版本", {}, listing,
                 untrusted_source=True, external_content=True),
            Tool("use_personal_skill", "按 ID 加载已启用的个人技能指令；不自动执行脚本或增加权限",
                 {"id": "个人技能 ID"}, use, untrusted_source=True, external_content=True)]
