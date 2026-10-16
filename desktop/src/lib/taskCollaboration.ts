import type { TaskItem } from "../types";

export type CollaborationAction = "review" | "followup" | "assess";

export function taskCollaborationDraft(task: TaskItem, action: CollaborationAction): { sid: string; text: string } | null {
  if (task.dependencies?.result_valid === false) return null;
  if (task.kind !== "delegation" || !task.collaboration || !task.owner_session?.startsWith("sid-")) return null;
  const sid = task.owner_session.slice(4);
  if (!/^[A-Za-z0-9._-]{1,120}$/.test(sid) || !/^task-[A-Za-z0-9_-]+$/.test(task.id)) return null;
  const round = task.collaboration.round;
  if (!Number.isInteger(round) || round < 1 || round > 3) return null;
  if (action === "assess") {
    if (!["done", "failed", "interrupted", "cancelled"].includes(task.status) || task.collaboration.review === "accepted") return null;
    return { sid, text: `请先读取任务 ${task.id} 第 ${round} 轮的最新 task_status，再用 task_model_assess 比较继续原模型与已配置候选的质量、耗时和总费用。证据不足请列出缺口；如需额外模型评判，遵守原确认门。仅给建议，不自动换模型、返工或验收。` };
  }
  if (action === "review") {
    if (task.status !== "done" || task.collaboration.review !== "pending") return null;
    return { sid, text: `请检查任务 ${task.id} 第 ${round} 轮的结果和验收证据，再用 task_review 记录接受或返工及具体理由。请先读取最新 task_status；执行结束不等于验收通过。` };
  }
  if (!["done", "failed", "interrupted", "cancelled"].includes(task.status)
      || task.collaboration.review === "accepted" || round >= 3
      || ((task.dependencies?.requires?.length || task.dependencies?.failure)
        && task.dependencies?.resolution !== "consumed")) return null;
  return { sid, text: `请先读取任务 ${task.id} 的最新 task_status，再用 task_followup 向原执行 Agent 提交第 ${round} 轮的补充或返工要求。保持原任务范围与角色。
补充要求：` };
}
