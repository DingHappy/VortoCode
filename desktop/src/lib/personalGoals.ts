import type { GatewayClient } from "../gateway";
import type { PersonalGoal } from "../types";

export type PersonalGoalClient = Pick<GatewayClient, "listPersonalGoals" | "createPersonalGoal" | "updatePersonalGoal" | "confirmPersonalGoal">;

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
