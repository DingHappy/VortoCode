import type { GatewayClient } from "../gateway";
import type { CodexProgressSnapshot, PersonalGoal } from "../types";
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
  client: () => Pick<GatewayClient, "listPersonalGoals" | "getCodexProgress"> | null;
  current: () => boolean;
  goals: (source: WorkdaySource<PersonalGoal[]>) => void;
  codex: (source: WorkdaySource<CodexProgressSnapshot | null>) => void;
  now?: () => number;
}) {
  let epoch = 0, pending: Promise<void> | null = null;
  let owner: ReturnType<typeof deps.client> = null;
  let goals: WorkdaySource<PersonalGoal[]> = { status: "loading", data: [] };
  let codex: WorkdaySource<CodexProgressSnapshot | null> = { status: "loading", data: null };
  const refresh = () => {
    const client = deps.client();
    if (!client || !deps.current()) return Promise.resolve();
    if (owner !== client) {
      owner = client; epoch++; pending = null;
      goals = { status: "loading", data: [] }; codex = { status: "loading", data: null };
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
    ]).then(() => {}).finally(() => { if (pending === task) pending = null; });
    pending = task;
    return task;
  };
  const invalidate = () => { epoch++; pending = null; };
  return { refresh, invalidate };
}
