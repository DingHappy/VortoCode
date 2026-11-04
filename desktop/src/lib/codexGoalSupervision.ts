import type { CodexProgressSnapshot, PersonalGoal } from "../types";
import { readExecutionObservation } from "./executionObservation";
import { hasConfirmedPersonalCriterion, hasConfirmedPersonalGoal } from "./personalGoalSnapshot";
import { freshAt } from "./readFreshness";

type State = "active" | "paused" | "awaiting_user" | "confirmed" | "unknown";
export interface CodexGoalProgress {
  goalId: string; threadId: string; state: State; reason: string;
  title: string; threadName: string; directory: string; criteria: string[];
  used?: number; limit?: number; canOpenGoal: boolean;
}
interface RecordWire {
  goal_id: string; thread_id: string; state: State; reason: string;
  revision?: number; used?: number; limit?: number; criterion_ids?: string[]; pending?: string[];
}
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown, max: number): v is string => typeof v === "string" && Boolean(v.trim()) && v.length <= max;
const integer = (v: unknown, min: number, max: number): v is number => Number.isSafeInteger(v) && Number(v) >= min && Number(v) <= max;
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.length <= 20 && v.every(i => text(i, 100)) && new Set(v).size === v.length;
const sameIds = (a: string[], b: string[]) => a.length === b.length && a.every(id => b.includes(id));
const UNKNOWN = "目标跟进状态待核对，请刷新后查看原目标和 Codex 会话。";
export const CODEX_REPORT_FRESH_MS = 30 * 60_000;

export function codexProgressFresh(source: { status: string; checkedAt?: number; data: CodexProgressSnapshot | null }, connected: boolean, now: number): boolean {
  return connected && source.status === "current" && freshAt(source.checkedAt, now)
    && freshAt(Date.parse(source.data?.received_at ?? ""), now, CODEX_REPORT_FRESH_MS);
}

/** Read only: no task state, acceptance, permission or execution is inferred here. */
export function codexGoalProgress(input: {
  snapshot: CodexProgressSnapshot | null; goals: PersonalGoal[];
  codexCurrent: boolean; goalsCurrent: boolean; now: number;
}): { items: CodexGoalProgress[]; warnings: string[] } {
  const raw = input.snapshot?.goal_supervision;
  if (raw === undefined) return { items: [], warnings: [] }; // Old resident host.
  const warnings: string[] = [];
  if (!Array.isArray(raw) || raw.length > 12) return { items: [], warnings: ["目标与 Codex 的关联记录无法核对。"] };
  const rows: RecordWire[] = [];
  for (const value of raw) {
    if (!object(value) || !text(value.goal_id, 100) || !/^personal-[A-Za-z0-9_-]{1,64}$/.test(value.goal_id)
        || !text(value.thread_id, 80)) { warnings.push("部分目标关联记录无法定位，请刷新核对。"); continue; }
    const base = { goal_id: value.goal_id, thread_id: value.thread_id, state: "unknown" as const, reason: UNKNOWN };
    const states = ["active", "paused", "awaiting_user", "confirmed", "unknown"];
    const complete = integer(value.revision, 1, Number.MAX_SAFE_INTEGER) && integer(value.used, 0, 12)
      && integer(value.limit, 1, 12) && ids(value.criterion_ids) && value.criterion_ids.length > 0 && ids(value.pending);
    if (!text(value.state, 80) || !states.includes(value.state) || !text(value.reason, 1000)
        || (value.state !== "unknown" && !complete)) {
      warnings.push("部分目标跟进信息不完整，已标为待核对。"); rows.push(base); continue;
    }
    rows.push({ ...base, state: value.state as State, reason: value.reason,
      revision: complete ? value.revision as number : undefined,
      used: complete ? value.used as number : undefined, limit: complete ? value.limit as number : undefined,
      criterion_ids: complete ? value.criterion_ids as string[] : undefined, pending: complete ? value.pending as string[] : undefined });
  }
  const items = rows.map(row => {
    const goals = input.goals.filter(g => g.id === row.goal_id);
    const threads = input.snapshot?.threads.filter(t => t.id === row.thread_id) ?? [];
    const unique = goals.length === 1 && threads.length === 1
      && rows.filter(r => r.goal_id === row.goal_id).length === 1 && rows.filter(r => r.thread_id === row.thread_id).length === 1;
    const goal = goals.length === 1 ? goals[0] : undefined, thread = threads.length === 1 ? threads[0] : undefined;
    const observation = readExecutionObservation(thread?.execution);
    const nativeState = thread?.status === "active" && thread.turn?.status === "inProgress"
      ? thread.flags.some(flag => ["waitingOnApproval", "waitingOnUserInput"].includes(flag)) ? "waiting_user" : "running"
      : thread?.status === "idle" ? ({ completed: "finished", failed: "failed", interrupted: "cancelled" } as Record<string, string>)[thread.turn?.status ?? ""] : undefined;
    const observed = observation !== null && observation.ref.backend_id === "codex" && observation.ref.session_id === row.thread_id
      && observation.ref.turn_id === thread?.turn?.id && observation.ref.run_id === thread?.turn?.id && !observation.ref.task_id
      && observation.state !== "unknown" && observation.state === nativeState
      && freshAt(observation.observed_at * 1000, input.now, CODEX_REPORT_FRESH_MS)
      && freshAt(Date.parse(input.snapshot?.received_at ?? ""), input.now, CODEX_REPORT_FRESH_MS);
    const criterionIds = goal?.acceptance_criteria.map(c => c.id) ?? [];
    const pending = goal?.acceptance_criteria.filter(c => !hasConfirmedPersonalCriterion(goal!, c)).map(c => c.id) ?? [];
    const correlated = unique && goal?.kind === "personal" && ["active", "blocked", "achieved"].includes(goal.status)
      && input.goalsCurrent && input.codexCurrent && observed && row.revision === goal?.revision
      && row.criterion_ids !== undefined && row.criterion_ids.every(id => criterionIds.includes(id))
      && row.pending !== undefined && sameIds(row.pending, pending);
    let state: State = correlated ? row.state : "unknown";
    if (state === "confirmed" && (!goal || !hasConfirmedPersonalGoal(goal))) state = "unknown";
    const known = state !== "unknown";
    return { goalId: row.goal_id, threadId: row.thread_id, state, reason: known ? row.reason : UNKNOWN,
      title: unique ? goal?.objective ?? "关联目标待核对" : "关联目标待核对",
      threadName: unique ? thread?.name ?? "Codex 会话待核对" : "Codex 会话待核对", directory: unique ? thread?.cwd ?? "" : "",
      criteria: known ? (row.criterion_ids ?? []).map(id => goal!.acceptance_criteria.find(c => c.id === id)!.text) : [],
      used: unique ? row.used : undefined, limit: unique ? row.limit : undefined,
      canOpenGoal: unique && input.goalsCurrent && goal !== undefined };
  });
  return { items, warnings: [...new Set(warnings)] };
}

export function goalProgressSummary(item: CodexGoalProgress): string {
  const count = item.used !== undefined && item.limit !== undefined ? `；${item.state === "unknown" ? "上次" : ""}已判断 ${item.used} / ${item.limit} 次` : "";
  return `Codex「${item.threadName}」：${item.reason}${count}`;
}
