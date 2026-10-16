import { describe, expect, it, vi } from "vitest";
import { goalDate, localGoalDate, personalGoalActions, type PersonalGoalClient } from "./personalGoals";
import type { PersonalGoal } from "../types";

const personalGoal: PersonalGoal = { id: "personal-example", kind: "personal", revision: 1, objective: "整理旅行计划",
  acceptance_criteria: [{ id: "criterion-1", text: "确认预算", status: "pending", evidence_ids: [] }],
  constraints: [], non_goals: [], status: "active", task_ids: [], evidence: [],
  next_action: "查询日期", blocker: "", due_at: "", follow_up_at: "", overdue: false, follow_up_due: false,
  progress: { passed: 0, failed: 0, total: 1 },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

function setup(list = vi.fn(async () => [personalGoal])) {
  const client = { listPersonalGoals: list } as unknown as PersonalGoalClient;
  let active = true;
  const sinks = { goals: vi.fn(), busy: vi.fn(), error: vi.fn() };
  return { client, sinks, leave: () => { active = false; }, actions: personalGoalActions(client, () => active, sinks) };
}

describe("personal goal response scope", () => {
  it("does not publish a previous host's delayed read", async () => {
    const result = deferred<PersonalGoal[]>();
    const state = setup(vi.fn(() => result.promise));
    const reading = state.actions.refresh();
    state.leave(); result.resolve([personalGoal]); await reading;
    expect(state.sinks.goals).not.toHaveBeenCalled();
    expect(state.sinks.error).not.toHaveBeenCalled();
  });

  it("deduplicates a write until its authoritative refresh finishes", async () => {
    const result = deferred<PersonalGoal>();
    const state = setup();
    const run = vi.fn(() => result.promise);
    const first = state.actions.write(run, personalGoal.id);
    expect(await state.actions.write(run, personalGoal.id)).toBe(false);
    result.resolve(personalGoal);
    expect(await first).toBe(true);
    expect(run).toHaveBeenCalledTimes(1);
    expect(state.sinks.goals).toHaveBeenCalledWith([personalGoal]);
    expect(state.sinks.busy.mock.calls).toEqual([[true], [false]]);
  });

  it("invalidates a read started before a write", async () => {
    const result = deferred<PersonalGoal[]>();
    const list = vi.fn(async () => [personalGoal]);
    list.mockImplementationOnce(() => result.promise);
    const state = setup(list);
    const reading = state.actions.refresh();
    await state.actions.write(async () => personalGoal, personalGoal.id);
    result.resolve([{ ...personalGoal, objective: "旧内容" }]); await reading;
    expect(state.sinks.goals).toHaveBeenCalledTimes(1);
    expect(state.sinks.goals).toHaveBeenLastCalledWith([personalGoal]);
  });

  it("does not refresh or report success after leaving a pending write", async () => {
    const result = deferred<PersonalGoal>();
    const state = setup();
    const writing = state.actions.write(() => result.promise, personalGoal.id);
    state.actions.dispose(); result.resolve(personalGoal);
    expect(await writing).toBe(false);
    expect(state.client.listPersonalGoals).not.toHaveBeenCalled();
    expect(state.sinks.busy).toHaveBeenCalledTimes(1);
  });

  it("rejects a different goal returned by a write", async () => {
    const state = setup();
    expect(await state.actions.write(async () => ({ ...personalGoal, id: "personal-other" }), personalGoal.id)).toBe(false);
    expect(state.sinks.error).toHaveBeenLastCalledWith(expect.stringContaining("身份不匹配"));
    expect(state.client.listPersonalGoals).not.toHaveBeenCalled();
  });

  it("keeps the form when saving succeeded but the following refresh failed", async () => {
    const state = setup(vi.fn(async () => { throw new Error("断线，请刷新确认"); }));
    expect(await state.actions.write(async () => personalGoal, personalGoal.id)).toBe(false);
    expect(state.sinks.error).toHaveBeenLastCalledWith("断线，请刷新确认");
  });

  it("returns failures to the current host and permits a deliberate retry", async () => {
    const state = setup();
    expect(await state.actions.write(async () => { throw new Error("目标已变化"); }, personalGoal.id)).toBe(false);
    expect(state.sinks.error).toHaveBeenLastCalledWith("目标已变化");
    expect(await state.actions.write(async () => personalGoal, personalGoal.id)).toBe(true);
  });

  it("preserves absolute instants when editing local date fields", () => {
    const stamp = "2026-10-18T09:15:00.000Z";
    expect(goalDate(localGoalDate(stamp))).toBe(stamp);
    expect(goalDate("")).toBe("");
    expect(localGoalDate("")).toBe("");
  });
});
