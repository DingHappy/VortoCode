import type { PersonalSkill, PersonalSkillDraft, SkillLearningSource } from "../types";

/** A reviewable checklist, not a fabricated recipe inferred from execution logs. */
export function skillLearningDraft(source: SkillLearningSource, existing?: PersonalSkill): PersonalSkillDraft {
  const checklist = ["## 处理目标（由任务材料起草，请人工提炼）", source.goal, "", "## 验证清单",
    ...source.criteria.map(text => `- ${text}`), "", "## 执行边界",
    "先确认本次目标和授权范围；执行仍遵守当前工具权限和人工确认。",
    "验证失败、材料不足或操作结果不确定时暂停并报告，不循环重试。"].join("\n");
  return {
    name: existing?.name || source.title.slice(0, 80),
    description: existing?.description || "从已验证并审阅的任务起草，使用前须人工提炼。",
    instructions: existing ? `${existing.instructions || ""}\n\n${checklist}` : checklist,
    revision: existing?.revision || null,
  };
}
