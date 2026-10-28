import type { GatewayClient } from "../gateway";
import type { AssistantScheduleItem, CodexProgressSnapshot, MemoryProposal, PersonalGoal } from "../types";
import type { WorkdaySource } from "./assistantWorkday";
import { startPolling } from "./polling";
import { readPersonalGoalSnapshot } from "./personalGoalSnapshot";

/** Expiry and day boundaries must advance even while a source read is pending. */
export function startWorkdayClock(update: (now: Date) => void,
  visibility: Pick<Document, "hidden" | "addEventListener" | "removeEventListener">) {
  return startPolling(async () => { update(new Date()); }, {
    intervalMs: 1_000, hiddenIntervalMs: 30_000, immediate: true, visibility,
  });
}

export function assistantWorkdayReads(deps: {
  client: () => (Pick<GatewayClient, "listPersonalGoals" | "getCodexProgress">
    & Partial<Pick<GatewayClient, "getAssistantSchedule" | "listMemoryProposals">>) | null;
  current: () => boolean;
  goals: (source: WorkdaySource<PersonalGoal[]>) => void;
  codex: (source: WorkdaySource<CodexProgressSnapshot | null>) => void;
  /** 定时任务：最近一次失败或超时的要进「等你决定」 */
  schedule?: (source: WorkdaySource<AssistantScheduleItem[]>) => void;
  /** 等你确认的候选记忆 */
  proposals?: (source: WorkdaySource<MemoryProposal[]>) => void;
  now?: () => number;
}) {
  let epoch = 0, pending: Promise<void> | null = null;
  let owner: ReturnType<typeof deps.client> = null;
  let goals: WorkdaySource<PersonalGoal[]> = { status: "loading", data: [] };
  let codex: WorkdaySource<CodexProgressSnapshot | null> = { status: "loading", data: null };
  let schedule: WorkdaySource<AssistantScheduleItem[]> = { status: "loading", data: [] };
  let proposals: WorkdaySource<MemoryProposal[]> = { status: "loading", data: [] };
  const refresh = () => {
    const client = deps.client();
    if (!client || !deps.current()) return Promise.resolve();
    if (owner !== client) {
      owner = client; epoch++; pending = null;
      goals = { status: "loading", data: [] }; codex = { status: "loading", data: null };
      schedule = { status: "loading", data: [] }; proposals = { status: "loading", data: [] };
    }
    if (pending) return pending;
    const version = epoch;
    const current = () => deps.current() && deps.client() === client && epoch === version;
    const checked = () => (deps.now ?? Date.now)();
    const task = Promise.allSettled([
      client.listPersonalGoals().then(value => {
        const data = readPersonalGoalSnapshot(value);
        if (current()) deps.goals(goals = { status: "current", data, checkedAt: checked() });
      }).catch(() => { if (current()) deps.goals(goals = { ...goals, status: "unavailable" }); }),
      client.getCodexProgress().then(data => {
        if (!data || !Array.isArray(data.threads) || data.threads.some(thread => !thread
            || typeof thread.id !== "string" || typeof thread.name !== "string" || typeof thread.status !== "string"
            || !Array.isArray(thread.flags) || thread.flags.some(flag => typeof flag !== "string")
            || (thread.turn != null && (typeof thread.turn.id !== "string" || typeof thread.turn.status !== "string"
              || [thread.turn.plan, thread.turn.request, thread.turn.reply, thread.turn.error].some(value => value !== undefined && typeof value !== "string"))))) throw new Error("Invalid Codex snapshot");
        if (current()) deps.codex(codex = { status: "current", data, checkedAt: checked() });
      }).catch(() => { if (current()) deps.codex(codex = { ...codex, status: "unavailable" }); }),
      // 旧版服务没有这两个接口时记为不可用，不当成「没有失败、没有待确认」
      Promise.resolve().then(() => {
        if (!deps.schedule) return;
        if (!client.getAssistantSchedule) throw new Error("unsupported");
        return client.getAssistantSchedule().then(data => {
          if (!Array.isArray(data) || data.some(item => !item || typeof item.id !== "string" || typeof item.title !== "string"
              || typeof item.enabled !== "boolean")) throw new Error("Invalid schedule snapshot");
          if (current()) deps.schedule!(schedule = { status: "current", data, checkedAt: checked() });
        });
      }).catch(() => { if (current()) deps.schedule?.(schedule = { ...schedule, status: "unavailable" }); }),
      Promise.resolve().then(() => {
        if (!deps.proposals) return;
        if (!client.listMemoryProposals) throw new Error("unsupported");
        return client.listMemoryProposals().then(data => {
          if (!Array.isArray(data) || data.some(item => !item || typeof item.id !== "string" || typeof item.content !== "string"))
            throw new Error("Invalid proposal snapshot");
          if (current()) deps.proposals!(proposals = { status: "current", data, checkedAt: checked() });
        });
      }).catch(() => { if (current()) deps.proposals?.(proposals = { ...proposals, status: "unavailable" }); }),
    ]).then(() => {}).finally(() => { if (pending === task) pending = null; });
    pending = task;
    return task;
  };
  const invalidate = () => { epoch++; pending = null; };
  return { refresh, invalidate };
}
