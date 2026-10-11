import { statusLabel } from "./labels";
import type { DesktopRuntimeInbox, RuntimeInboxTask, TaskLifecycleSummary } from "../types";

export const BOARD_COLUMNS = [
  { id: "queued", label: "待开始" },
  { id: "working", label: "执行中" },
  { id: "attention", label: "待处理" },
  { id: "completed", label: "已完成" },
] as const;
export type BoardColumn = typeof BOARD_COLUMNS[number]["id"] | "history";
export type BoardKind = "session" | "task" | "goal" | "decision";
export type BoardFilters = { runtimeId?: string; sessionKey?: string; goalKey?: string };
export type BoardItem = {
  key: string; source: DesktopRuntimeInbox; kind: BoardKind; id: string;
  title: string; detail: string; column: BoardColumn; status: string;
  sessionId?: string; goalId?: string; action: string; lifecycle?: TaskLifecycleSummary;
};
export const boardIdentity = (runtimeId: string, id: string) => JSON.stringify([runtimeId, id]);

/** Session-store IDs are raw; owner identities use exactly one sid- prefix. */
export const boardSessionId = (owner: string) => owner.startsWith("sid-") ? owner.slice(4) : owner;

export function validTaskLifecycle(task: RuntimeInboxTask): TaskLifecycleSummary | undefined {
  const value = task.lifecycle;
  if (!value || value.version !== 1 || value.execution !== task.status || typeof value.kind !== "string"
    || !["queued", "working", "attention", "completed", "history"].includes(value.column)
    || !["unknown", "not_applicable", "not_submitted", "pending", "accepted", "rework_requested"].includes(value.review)
    || !["unknown", "not_applicable", "pending", "handled"].includes(value.handling)
    || !["unknown", "not_applicable", "valid", "invalid"].includes(value.validity)
    || !(value.round === null || Number.isInteger(value.round) && value.round >= 1 && value.round <= 3)
    || typeof value.revision !== "string" || value.revision !== "" && !/^[a-f0-9]{64}$/.test(value.revision)
    || typeof value.label !== "string" || !value.label || value.label.length > 160
    || typeof value.recovered_by !== "string") return undefined;
  if (value.kind === "delegation" && value.review !== "unknown" && value.round === null) return undefined;
  if (["pending", "handled"].includes(value.handling) && !value.revision) return undefined;
  if (value.column === "completed" && !(task.status === "done" && ["valid", "not_applicable"].includes(value.validity)
    && (value.kind === "delegation" && value.review === "accepted" && ["handled", "not_applicable"].includes(value.handling)
      || ["dev", "dev-resume"].includes(value.kind) && value.review === "not_applicable" && value.handling === "handled"))) return undefined;
  if (value.column === "queued" && (task.status !== "queued" || value.validity === "invalid")) return undefined;
  if (value.column === "working" && (!["running", "cancelling"].includes(task.status) || value.validity === "invalid")) return undefined;
  if (value.column === "history" && (!value.recovered_by || !["failed", "cancelled", "paused", "interrupted"].includes(task.status))) return undefined;
  return value;
}

/** A bounded Desktop projection, never an execution/acceptance state machine. */
export function buildTaskBoard(sources: DesktopRuntimeInbox[], filters: BoardFilters = {}): BoardItem[] {
  const items: BoardItem[] = [];
  for (const source of sources) {
    if (filters.runtimeId && source.runtimeId !== filters.runtimeId) continue;
    const snapshot = source.snapshot;
    if (!snapshot || snapshot.version !== 1) continue;
    const add = (item: Omit<BoardItem, "key" | "source">) => {
      if (filters.sessionKey && (!item.sessionId || boardIdentity(source.runtimeId, item.sessionId) !== filters.sessionKey)) return;
      if (filters.goalKey && (!item.goalId || boardIdentity(source.runtimeId, item.goalId) !== filters.goalKey)) return;
      items.push({ ...item, source, key: JSON.stringify([source.runtimeId, item.kind, item.id]) });
    };
    for (const session of snapshot.sessions) {
      if (["idle", "inactive"].includes(session.status)) continue;
      add({ kind: "session", id: session.sid, sessionId: session.sid, title: session.title || "未命名会话",
        detail: session.activity || session.running_prompt || "打开来源会话查看详情",
        column: session.status === "queued" ? "queued" : session.status === "working" ? "working"
          : session.status === "completed" ? "completed" : "attention",
        status: session.status === "completed" ? "会话已结束 · 不代表目标达成" : session.status === "needs_input" ? "等待答复或确认" : session.status === "working" ? "会话执行中" : statusLabel(session.status),
        action: "打开会话" });
    }
    for (const goal of snapshot.goals) {
      add({ kind: "goal", id: goal.id, goalId: goal.id, title: goal.objective || "未命名目标",
        detail: `${goal.progress.passed}/${goal.progress.total} 条验收标准通过${goal.blocker ? ` · ${goal.blocker}` : goal.next_action ? ` · ${goal.next_action}` : ""}`,
        column: goal.status === "draft" ? "queued" : goal.status === "active" ? "working"
          : goal.status === "achieved" ? "completed" : "attention",
        status: goal.status === "achieved" ? "目标已达成" : statusLabel(goal.status), action: "查看目标" });
    }
    for (const task of snapshot.tasks) {
      const lifecycle = validTaskLifecycle(task);
      add({ kind: "task", id: task.id, sessionId: boardSessionId(task.owner_session), lifecycle, goalId: task.goal_id,
        title: task.prompt || "后台任务", detail: lifecycle ? `${lifecycle.label}；验收、交接与目标达成分别记录` : task.status === "done"
          ? "执行已结束；验收、交接与结果有效性请在原任务核对" : task.detail || task.branch || "查看原任务状态",
        column: lifecycle?.column ?? (task.status === "queued" ? "queued" : ["running", "cancelling"].includes(task.status) ? "working" : "attention"),
        status: lifecycle?.label ?? (task.status === "done" ? "执行结束 · 待核对" : task.status === "waiting" ? "等待前置验收" : task.status === "cancelled" ? "已取消 · 查看记录" : statusLabel(task.status)),
        action: "查看任务" });
    }
    for (const decision of snapshot.decisions) {
      add({ kind: "decision", id: decision.id, sessionId: decision.session_id ? boardSessionId(decision.session_id) : undefined,
        goalId: decision.kind === "goal" ? decision.target_id : undefined,
        title: decision.title, detail: decision.detail || "等待你的处理", column: "attention",
        status: "待处理事项", action: "打开处理入口" });
    }
  }
  return items;
}
