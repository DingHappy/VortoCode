import type { GatewayClient } from "../gateway";
import type { PersonalGoal } from "../types";
import { readPersonalGoalSnapshot } from "./personalGoalSnapshot";

export type PersonalGoalClient = Pick<GatewayClient, "listPersonalGoals" | "createPersonalGoal" | "updatePersonalGoal" | "confirmPersonalGoal" | "configurePersonalGoalFollowUp" | "acknowledgePersonalGoalFollowUp">;

export function goalFollowUpLabel(goal: PersonalGoal): string {
  const state = goal.follow_up;
  switch (state?.status) {
    case "scheduled": return state.attempts ? "通知未受理，等待重试" : "已批准，等待到点";
    case "sending": return "通知处理中，结果尚未核对";
    case "accepted": return "通知已受理，未确认送达或已读";
    case "acknowledged": return "用户已确认看到提醒";
    case "unknown": return "通知结果不明，需要核对";
    case "failed": return "通知连续未受理，需要人工重试";
    case "cancelled": return "提醒已暂停或因目标变化失效";
    default: return state?.status ? "提醒状态待核对" : "提醒尚未批准";
  }
}

type GoalWriteCheck = { id: string; revision: number };
export type PersonalGoalOperation = { phase: "idle" } | { phase: "submitting"; goalId: string } | { phase: "accepted"; goalId: string }
  | { phase: "unconfirmed"; goalId: string; checked: boolean };
type GoalOperation = {
  pending: { id: string; settled: Promise<void> } | null;
  awaiting: GoalWriteCheck | null;
  unconfirmed: { id: string; checked: boolean } | null;
  epoch: number;
  listeners: Set<() => void>;
};
// Coordination only: no goal data, input text, credentials or durable retry queue.
const operations = new WeakMap<PersonalGoalClient, GoalOperation>();

/** One write per client, including time spent away from the goal panel. */
export function personalGoalActions(client: PersonalGoalClient, current: () => boolean, sinks: {
  goals: (goals: PersonalGoal[]) => void;
  busy: (busy: boolean) => void;
  error: (error: string) => void;
  verification?: (required: boolean) => void;
  reading?: (reading: boolean) => void;
  operation?: (operation: PersonalGoalOperation) => void;
}) {
  let epoch = 0;
  let pending = false;
  let alive = true;
  let operation = operations.get(client);
  if (!operation) {
    operation = { pending: null, awaiting: null, unconfirmed: null, epoch: 0, listeners: new Set() };
    operations.set(client, operation);
  }
  const shared = operation;
  let readError = "";
  let reading: { epoch: number; promise: Promise<boolean> } | null = null;
  let lastBusy = false, lastVerification = false;
  let lastOperation = "";
  const active = () => alive && current();
  const sync = () => {
    if (!active()) return;
    const busy = pending || shared.pending !== null, verification = shared.awaiting !== null;
    if (busy !== lastBusy) { lastBusy = busy; sinks.busy(busy); }
    if (verification !== lastVerification) { lastVerification = verification; sinks.verification?.(verification); }
    const operation: PersonalGoalOperation = shared.pending ? { phase: "submitting", goalId: shared.pending.id }
      : shared.awaiting ? { phase: "accepted", goalId: shared.awaiting.id }
      : shared.unconfirmed ? { phase: "unconfirmed", goalId: shared.unconfirmed.id, checked: shared.unconfirmed.checked } : { phase: "idle" };
    const key = JSON.stringify(operation);
    if (key !== lastOperation) { lastOperation = key; sinks.operation?.(operation); }
  };
  shared.listeners.add(sync);
  const notify = () => shared.listeners.forEach(listener => listener());
  const report = (error: unknown) => { if (active()) sinks.error(error instanceof Error ? error.message : String(error)); };
  const check = (goal: PersonalGoal) => {
    if (!goal || goal.kind !== "personal" || typeof goal.id !== "string" || !goal.id.startsWith("personal-") || !Number.isSafeInteger(goal.revision) || goal.revision < 1) {
      throw new Error("目标响应身份不匹配，请刷新核对");
    }
  };
  function refresh(): Promise<boolean> {
    if (!active()) return Promise.resolve(false);
    if (reading?.epoch === epoch) return reading.promise;
    const attempt = ++epoch;
    sync();
    sinks.reading?.(true);
    const promise = (async () => {
      try {
        // Leaving a page does not cancel its write. Read only after it settles.
        if (shared.pending) await shared.pending.settled;
        if (!active() || attempt !== epoch) return false;
        const generation = shared.epoch, expected = shared.awaiting, unconfirmed = shared.unconfirmed;
        const value = await client.listPersonalGoals();
        if (!active() || attempt !== epoch) return false;
        if (generation !== shared.epoch) throw new Error("目标操作已变化，请刷新核对最新状态");
        const goals = readPersonalGoalSnapshot(value);
        if (expected) {
          const actual = goals.find(goal => goal.id === expected.id);
          if (!actual || actual.revision < expected.revision) throw new Error("最新读取尚未包含已提交的目标版本");
        }
        sinks.goals(goals); sinks.error("");
        if (shared.awaiting === expected) shared.awaiting = null;
        if (unconfirmed && shared.unconfirmed === unconfirmed) unconfirmed.checked = true;
        readError = ""; notify();
        return true;
      } catch (error) {
        if (attempt === epoch) {
          readError = error instanceof Error ? error.message : String(error);
          report(error);
        }
        return false;
      }
    })().finally(() => {
      if (reading?.epoch === attempt) reading = null;
      if (active() && attempt === epoch) sinks.reading?.(false);
    });
    reading = { epoch: attempt, promise };
    return promise;
  }
  return {
    refresh,
    dispose() { alive = false; epoch += 1; shared.listeners.delete(sync); },
    async write(run: () => Promise<PersonalGoal>, expectedId: string): Promise<boolean> {
      if (!active() || pending || shared.pending) return false;
      if (shared.awaiting) {
        report(new Error("上次操作已提交，请先刷新核对最新状态，不要重复提交"));
        return false;
      }
      if (shared.unconfirmed && !shared.unconfirmed.checked) {
        report(new Error("上次提交未取得成功回执，请先刷新目标并核对，不要直接重复提交"));
        return false;
      }
      pending = true; epoch += 1; shared.epoch += 1;
      shared.unconfirmed = null;
      let settled!: () => void;
      const completion = new Promise<void>(resolve => { settled = resolve; });
      shared.pending = { id: expectedId, settled: completion };
      sinks.reading?.(false); sinks.error(""); notify();
      try {
        try {
          const goal = await run();
          check(goal);
          if (goal.id !== expectedId) throw new Error("目标响应身份不匹配，请刷新核对");
          // Keep the accepted version even if its originating panel has left.
          shared.awaiting = { id: goal.id, revision: goal.revision };
        } catch (error) {
          // A failed request is not proof that the remote write never happened.
          shared.unconfirmed = { id: expectedId, checked: false };
          throw error;
        } finally {
          shared.pending = null; shared.epoch += 1; settled(); notify();
        }
        if (!active()) return false;
        // A read started before acceptance must not replace the post-commit check.
        epoch += 1;
        readError = "";
        const readBack = await refresh();
        if (!active()) return false;
        const verified = readBack || shared.awaiting === null;
        if (!verified && active()) report(new Error(`操作已提交，但最新状态尚未核对；请先刷新，不要重复提交。${readError}`));
        return verified;
      } catch (error) { report(error); return false; }
      finally { pending = false; sync(); }
    },
  };
}

export function localGoalDate(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function goalDate(value: string): string {
  return value ? new Date(value).toISOString() : "";
}
