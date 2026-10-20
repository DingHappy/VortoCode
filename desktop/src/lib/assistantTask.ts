import type { AssistantDispatch } from "../types";

export type AssistantTaskAction = "approve" | "cancel" | "refresh" | "open" | "answer" | "open_pr";

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
