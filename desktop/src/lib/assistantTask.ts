import type { AssistantDispatch } from "../types";

export type AssistantTaskAction = "approve" | "cancel" | "refresh" | "open" | "answer" | "open_pr" | "confirm_goal";

export function assistantTaskActions(task: AssistantDispatch) {
  return {
    approve: task.status === "proposed",
    cancel: task.status === "proposed" || (task.status === "send_failed" && task.submission_attempted === false),
    retry: ["submission_unknown", "send_failed", "sending"].includes(task.status)
      && Boolean(task.request_id) && !task.legacy_submission,
    open: Boolean(task.remote_task_id) && !["proposed", "sending", "submission_unknown", "send_failed"].includes(task.status),
    // 旧方式下发的单子没有归属会话，只能到项目工作台回答
    answer: task.status === "waiting" && Boolean(task.question) && Boolean(task.remote_session),
    openPr: task.status === "done" && !task.error && !task.pr_url && task.branch.startsWith("vorto/")
      && task.pr_state !== "opening",
    // 挂了目标验收标准、成功做完、还没确认过：人点一下记为通过
    confirmGoal: task.status === "done" && !task.error && Boolean(task.goal_link?.criterion_id) && !task.goal_confirmed_at,
  };
}

/** Shared by both copies of a task card, including time spent in confirmation. */
export class AssistantActionGate {
  private readonly owners = new WeakMap<object, Set<string>>();

  async run(owner: object, scope: string, taskId: string, current: () => boolean, operation: () => Promise<void>): Promise<void> {
    if (!current()) return;
    let pending = this.owners.get(owner);
    if (!pending) { pending = new Set(); this.owners.set(owner, pending); }
    const key = JSON.stringify([scope, taskId]);
    if (pending.has(key)) throw new Error("该任务单正在处理，请稍后核对状态");
    pending.add(key);
    try { if (current()) await operation(); }
    finally { pending.delete(key); }
  }
}

/** 一句话进度：「3/5 步已落地，正在做：补单测」；没有进度时空串。和常驻电脑那边的 progress_line 同口径。 */
export function progressLine(task: AssistantDispatch): string {
  const progress = task.progress;
  if (!progress) return "";
  if (!progress.total) return progress.latest ? `还在拆计划：${progress.latest}` : "";
  let text = `${progress.landed}/${progress.total} 步已落地`;
  if (progress.failed) text += `，${progress.failed} 步失败`;
  if (progress.stage === "integrated") text += "，集成测试通过";
  else if (progress.stage === "integration_failed") text += "，集成测试没过";
  const running = progress.steps.filter(step => step.status === "running" && step.title).map(step => step.title);
  if (running.length) text += `，正在做：${running.slice(0, 2).join("、")}`;
  return text;
}
