import type { GatewayClient } from "../gateway";
import type { PersonalGoal } from "../types";

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
    default: return "提醒尚未批准";
  }
}

/** One pending write per visible assistant. Responses never migrate to another host. */
export function personalGoalActions(client: PersonalGoalClient, current: () => boolean, sinks: {
  goals: (goals: PersonalGoal[]) => void;
  busy: (busy: boolean) => void;
  error: (error: string) => void;
}) {
  let epoch = 0;
  let pending = false;
  let alive = true;
  const active = () => alive && current();
  const report = (error: unknown) => { if (active()) sinks.error(error instanceof Error ? error.message : String(error)); };
  const check = (goal: PersonalGoal) => {
    if (!goal || goal.kind !== "personal" || !goal.id.startsWith("personal-") || !Number.isSafeInteger(goal.revision) || goal.revision < 1) {
      throw new Error("目标响应身份不匹配，请刷新核对");
    }
  };
  async function refresh(): Promise<boolean> {
    if (!active()) return false;
    const attempt = ++epoch;
    try {
      const goals = await client.listPersonalGoals();
      if (!active() || attempt !== epoch) return false;
      goals.forEach(check);
      sinks.goals(goals); sinks.error("");
      return true;
    } catch (error) { if (attempt === epoch) report(error); return false; }
  }
  return {
    refresh,
    dispose() { alive = false; epoch += 1; },
    async write(run: () => Promise<PersonalGoal>, expectedId: string): Promise<boolean> {
      if (!active() || pending) return false;
      pending = true; epoch += 1; sinks.busy(true); sinks.error("");
      try {
        const goal = await run();
        if (!active()) return false;
        check(goal);
        if (goal.id !== expectedId) throw new Error("目标响应身份不匹配，请刷新核对");
        return await refresh();
      } catch (error) { report(error); return false; }
      finally { pending = false; if (active()) sinks.busy(false); }
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
