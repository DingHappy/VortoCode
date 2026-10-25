import { describe, expect, it, vi } from "vitest";
import { goalDate, goalFollowUpLabel, localGoalDate, personalGoalActions, type PersonalGoalClient } from "./personalGoals";
import type { PersonalGoal } from "../types";

const personalGoal: PersonalGoal = { id: "personal-example", kind: "personal", revision: 1, objective: "整理旅行计划",
  acceptance_criteria: [{ id: "criterion-1", text: "确认预算", status: "pending", evidence_ids: [] }],
  constraints: [], non_goals: [], status: "active", task_ids: [], evidence: [],
  next_action: "查询日期", blocker: "", due_at: "", follow_up_at: "", overdue: false, follow_up_due: false,
  progress: { passed: 0, failed: 0, total: 1 },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function setup(list = vi.fn(async () => [personalGoal])) {
  const client = { listPersonalGoals: list } as unknown as PersonalGoalClient;
  let active = true;
  const sinks = { goals: vi.fn(), busy: vi.fn(), error: vi.fn(), verification: vi.fn(), reading: vi.fn(), operation: vi.fn() };
  return { client, sinks, leave: () => { active = false; }, actions: personalGoalActions(client, () => active, sinks) };
}

function reopen(client: PersonalGoalClient) {
  const sinks = { goals: vi.fn(), busy: vi.fn(), error: vi.fn(), verification: vi.fn(), reading: vi.fn(), operation: vi.fn() };
  return { sinks, actions: personalGoalActions(client, () => true, sinks) };
}

describe("personal goal response scope", () => {
  it("shows failed and uncertain notification results without claiming delivery", () => {
    expect(goalFollowUpLabel({ ...personalGoal, follow_up: { status: "failed" } })).toContain("人工重试");
    expect(goalFollowUpLabel({ ...personalGoal, follow_up: { status: "sending" } })).toContain("尚未核对");
    expect(goalFollowUpLabel({ ...personalGoal, follow_up: { status: "cancelled" } })).toContain("失效");
  });
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
    expect(state.sinks.error).toHaveBeenLastCalledWith(expect.stringContaining("操作已提交"));
    expect(state.sinks.error).toHaveBeenLastCalledWith(expect.stringContaining("断线，请刷新确认"));
    expect(state.sinks.verification).toHaveBeenLastCalledWith(true);
  });

  it("returns failures to the current host and permits a deliberate retry after a current read", async () => {
    const state = setup();
    expect(await state.actions.write(async () => { throw new Error("目标已变化"); }, personalGoal.id)).toBe(false);
    expect(state.sinks.error).toHaveBeenLastCalledWith("目标已变化");
    expect(await state.actions.refresh()).toBe(true);
    expect(await state.actions.write(async () => personalGoal, personalGoal.id)).toBe(true);
  });

  it("blocks repeat writes after commit until a successful current read verifies the result", async () => {
    const list = vi.fn(async (): Promise<PersonalGoal[]> => { throw new Error("offline"); });
    const state = setup(list);
    const run = vi.fn(async () => personalGoal);
    expect(await state.actions.write(run, personalGoal.id)).toBe(false);
    expect(await state.actions.write(run, personalGoal.id)).toBe(false);
    expect(run).toHaveBeenCalledTimes(1);
    expect(state.sinks.error).toHaveBeenLastCalledWith(expect.stringContaining("不要重复提交"));
    list.mockImplementation(async () => [personalGoal]);
    expect(await state.actions.refresh()).toBe(true);
    expect(state.sinks.verification).toHaveBeenLastCalledWith(false);
    expect(await state.actions.write(run, personalGoal.id)).toBe(true);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it.each(["older revision", "missing goal", "duplicate goal"])("does not unlock after an unusable read-back: %s", async fault => {
    const updated = { ...personalGoal, revision: 2 };
    const list = vi.fn(async () => fault === "older revision" ? [personalGoal] : fault === "missing goal" ? [] : [updated, updated]);
    const state = setup(list);
    const run = vi.fn(async () => updated);
    expect(await state.actions.write(run, personalGoal.id)).toBe(false);
    expect(state.sinks.goals).not.toHaveBeenCalled();
    expect(await state.actions.write(run, personalGoal.id)).toBe(false);
    expect(run).toHaveBeenCalledTimes(1);
    list.mockResolvedValue([{ ...updated, revision: 3 }]);
    expect(await state.actions.refresh()).toBe(true);
    expect(state.sinks.verification).toHaveBeenLastCalledWith(false);
  });

  it("an overlapping old GET cannot verify a newer accepted write", async () => {
    const stale = deferred<PersonalGoal[]>(), commit = deferred<PersonalGoal>();
    const list = vi.fn(() => stale.promise);
    const state = setup(list);
    const writing = state.actions.write(() => commit.promise, personalGoal.id);
    const reading = state.actions.refresh();
    commit.resolve({ ...personalGoal, revision: 2 });
    await Promise.resolve();
    stale.resolve([personalGoal]);
    expect(await reading).toBe(false);
    expect(await writing).toBe(false);
    expect(state.sinks.goals).not.toHaveBeenCalled();
    expect(state.sinks.verification).toHaveBeenLastCalledWith(true);
    list.mockResolvedValue([{ ...personalGoal, revision: 2 }]);
    expect(await state.actions.refresh()).toBe(true);
  });

  it("leaving a post-commit read cannot unlock or update another host", async () => {
    const refresh = deferred<PersonalGoal[]>();
    const state = setup(vi.fn(() => refresh.promise));
    const writing = state.actions.write(async () => personalGoal, personalGoal.id);
    await Promise.resolve();
    state.actions.dispose(); refresh.resolve([personalGoal]);
    expect(await writing).toBe(false);
    expect(state.sinks.goals).not.toHaveBeenCalled();
    expect(state.sinks.verification.mock.calls).toEqual([[true]]);
  });

  it("a manual refresh joins the post-commit read instead of superseding it", async () => {
    const fresh = deferred<PersonalGoal[]>();
    const list = vi.fn(() => fresh.promise);
    const state = setup(list);
    const updated = { ...personalGoal, revision: 2 };
    const writing = state.actions.write(async () => updated, personalGoal.id);
    await Promise.resolve();
    const latest = state.actions.refresh();
    fresh.resolve([updated]);
    expect(await latest).toBe(true);
    expect(await writing).toBe(true);
    expect(list).toHaveBeenCalledTimes(1);
    expect(state.sinks.verification).toHaveBeenLastCalledWith(false);
    expect(state.sinks.error).toHaveBeenLastCalledWith("");
  });

  it("coalesces manual reads and closes the loading state once", async () => {
    const response = deferred<PersonalGoal[]>();
    const state = setup(vi.fn(() => response.promise));
    const first = state.actions.refresh(), second = state.actions.refresh();
    expect(second).toBe(first);
    expect(state.client.listPersonalGoals).toHaveBeenCalledTimes(1);
    expect(state.sinks.reading.mock.calls).toEqual([[true]]);
    response.resolve([personalGoal]);
    expect(await first).toBe(true);
    expect(state.sinks.reading.mock.calls).toEqual([[true], [false]]);
    expect(state.sinks.goals).toHaveBeenCalledTimes(1);
  });

  it("a failed or synchronously throwing read permits a fresh manual attempt", async () => {
    const list = vi.fn(async () => [personalGoal]);
    list.mockRejectedValueOnce(new Error("offline"));
    list.mockImplementationOnce(() => { throw new Error("bad transport"); });
    const state = setup(list);
    expect(await state.actions.refresh()).toBe(false);
    expect(await state.actions.refresh()).toBe(false);
    expect(await state.actions.refresh()).toBe(true);
    expect(list).toHaveBeenCalledTimes(3);
    expect(state.sinks.reading.mock.calls).toEqual([[true], [false], [true], [false], [true], [false]]);
  });

  it("a pre-write read cannot close the post-commit loading state", async () => {
    const stale = deferred<PersonalGoal[]>(), fresh = deferred<PersonalGoal[]>();
    const list = vi.fn(() => stale.promise).mockImplementationOnce(() => stale.promise).mockImplementationOnce(() => fresh.promise);
    const state = setup(list);
    const oldRead = state.actions.refresh();
    const writing = state.actions.write(async () => ({ ...personalGoal, revision: 2 }), personalGoal.id);
    await Promise.resolve();
    expect(list).toHaveBeenCalledTimes(2);
    stale.resolve([personalGoal]);
    expect(await oldRead).toBe(false);
    expect(state.sinks.reading).toHaveBeenLastCalledWith(true);
    expect(state.sinks.goals).not.toHaveBeenCalled();
    fresh.resolve([{ ...personalGoal, revision: 2 }]);
    expect(await writing).toBe(true);
    expect(state.sinks.reading).toHaveBeenLastCalledWith(false);
  });

  it("a departed controller cannot close the current connection's loading state", async () => {
    const response = deferred<PersonalGoal[]>();
    const state = setup(vi.fn(() => response.promise));
    const reading = state.actions.refresh();
    state.actions.dispose();
    response.resolve([personalGoal]);
    expect(await reading).toBe(false);
    expect(state.sinks.reading.mock.calls).toEqual([[true]]);
    expect(state.sinks.goals).not.toHaveBeenCalled();
  });

  it("starts a new read after acceptance even when a pre-commit read is still pending", async () => {
    const commit = deferred<PersonalGoal>(), before = deferred<PersonalGoal[]>(), after = deferred<PersonalGoal[]>();
    const list = vi.fn(() => before.promise).mockImplementationOnce(() => before.promise).mockImplementationOnce(() => after.promise);
    const state = setup(list);
    const updated = { ...personalGoal, revision: 2 };
    const preCommit = state.actions.refresh();
    const writing = state.actions.write(() => commit.promise, personalGoal.id);
    commit.resolve(updated);
    await Promise.resolve();
    expect(list).toHaveBeenCalledTimes(2);
    before.resolve([updated]);
    expect(await preCommit).toBe(false);
    expect(state.sinks.goals).not.toHaveBeenCalled();
    expect(state.sinks.verification).toHaveBeenLastCalledWith(true);
    after.resolve([updated]);
    expect(await writing).toBe(true);
    expect(state.sinks.goals).toHaveBeenCalledTimes(1);
  });

  it("a reopened panel waits for the departed panel's pending write instead of sending another", async () => {
    const commit = deferred<PersonalGoal>();
    const updated = { ...personalGoal, revision: 2 };
    const list = vi.fn(async () => [updated]);
    const first = setup(list), run = vi.fn(() => commit.promise);
    const writing = first.actions.write(run, personalGoal.id);
    first.actions.dispose();
    const next = reopen(first.client);
    const reading = next.actions.refresh();
    expect(next.sinks.busy).toHaveBeenLastCalledWith(true);
    expect(list).not.toHaveBeenCalled();
    expect(await next.actions.write(run, personalGoal.id)).toBe(false);
    expect(run).toHaveBeenCalledTimes(1);
    commit.resolve(updated);
    expect(await writing).toBe(false);
    expect(await reading).toBe(true);
    expect(next.sinks.goals).toHaveBeenLastCalledWith([updated]);
    expect(next.sinks.busy).toHaveBeenLastCalledWith(false);
    expect(next.sinks.verification).toHaveBeenLastCalledWith(false);
    expect(first.sinks.goals).not.toHaveBeenCalled();
    expect(run).toHaveBeenCalledTimes(1);
    next.actions.dispose();
  });

  it("retains an accepted version across panel disposal until a new panel verifies it", async () => {
    const updated = { ...personalGoal, revision: 2 };
    const list = vi.fn(async (): Promise<PersonalGoal[]> => { throw new Error("offline"); });
    const first = setup(list);
    expect(await first.actions.write(async () => updated, personalGoal.id)).toBe(false);
    first.actions.dispose();
    const next = reopen(first.client);
    list.mockResolvedValue([personalGoal]);
    expect(await next.actions.refresh()).toBe(false);
    expect(next.sinks.verification).toHaveBeenLastCalledWith(true);
    expect(next.sinks.goals).not.toHaveBeenCalled();
    const retry = vi.fn(async () => updated);
    expect(await next.actions.write(retry, personalGoal.id)).toBe(false);
    expect(retry).not.toHaveBeenCalled();
    list.mockResolvedValue([updated]);
    expect(await next.actions.refresh()).toBe(true);
    expect(next.sinks.verification).toHaveBeenLastCalledWith(false);
    next.actions.dispose();
  });

  it("records acceptance that arrives after disposal without updating the old panel", async () => {
    const commit = deferred<PersonalGoal>();
    const first = setup();
    const writing = first.actions.write(() => commit.promise, personalGoal.id);
    first.actions.dispose();
    commit.resolve({ ...personalGoal, revision: 2 });
    expect(await writing).toBe(false);
    expect(first.client.listPersonalGoals).not.toHaveBeenCalled();
    const next = reopen(first.client);
    expect(await next.actions.refresh()).toBe(false);
    expect(next.sinks.verification).toHaveBeenLastCalledWith(true);
    expect(first.sinks.verification).not.toHaveBeenCalled();
    next.actions.dispose();
  });

  it("separates two clients with the same goal ID while one write is still pending", async () => {
    const commit = deferred<PersonalGoal>();
    const first = setup(), second = setup();
    const writing = first.actions.write(() => commit.promise, personalGoal.id);
    const run = vi.fn(async () => personalGoal);
    expect(await second.actions.write(run, personalGoal.id)).toBe(true);
    expect(run).toHaveBeenCalledTimes(1);
    expect(first.sinks.busy).toHaveBeenLastCalledWith(true);
    first.actions.dispose(); second.actions.dispose();
    commit.resolve(personalGoal); await writing;
  });

  it("another panel's pre-write read cannot verify or unlock an accepted write", async () => {
    const before = deferred<PersonalGoal[]>(), after = deferred<PersonalGoal[]>();
    const list = vi.fn(() => before.promise).mockImplementationOnce(() => before.promise).mockImplementationOnce(() => after.promise);
    const first = setup(list), other = reopen(first.client);
    const oldRead = other.actions.refresh();
    const updated = { ...personalGoal, revision: 2 };
    const writing = first.actions.write(async () => updated, personalGoal.id);
    await Promise.resolve();
    before.resolve([updated]);
    expect(await oldRead).toBe(false);
    expect(other.sinks.goals).not.toHaveBeenCalled();
    expect(other.sinks.error).toHaveBeenLastCalledWith(expect.stringContaining("目标操作已变化，请刷新"));
    const retry = vi.fn(async () => updated);
    expect(await other.actions.write(retry, personalGoal.id)).toBe(false);
    expect(retry).not.toHaveBeenCalled();
    expect(other.sinks.verification).toHaveBeenLastCalledWith(true);
    after.resolve([updated]);
    expect(await writing).toBe(true);
    expect(other.sinks.verification).toHaveBeenLastCalledWith(false);
    first.actions.dispose(); other.actions.dispose();
  });

  it("a departed panel does not report success when a new panel verifies its receipt", async () => {
    const oldRead = deferred<PersonalGoal[]>();
    const updated = { ...personalGoal, revision: 2 };
    const list = vi.fn(async () => [updated]).mockImplementationOnce(() => oldRead.promise);
    const first = setup(list);
    const writing = first.actions.write(async () => updated, personalGoal.id);
    await Promise.resolve();
    first.actions.dispose();
    const next = reopen(first.client);
    expect(await next.actions.refresh()).toBe(true);
    oldRead.resolve([updated]);
    expect(await writing).toBe(false);
    expect(first.sinks.goals).not.toHaveBeenCalled();
    expect(next.sinks.goals).toHaveBeenCalledWith([updated]);
    next.actions.dispose();
  });

  it("a departed request's failure releases the waiting read without replaying the write", async () => {
    const commit = deferred<PersonalGoal>();
    const first = setup(), run = vi.fn(() => commit.promise);
    const writing = first.actions.write(run, personalGoal.id);
    first.actions.dispose();
    const next = reopen(first.client);
    const reading = next.actions.refresh();
    expect(first.client.listPersonalGoals).not.toHaveBeenCalled();
    commit.reject(new Error("transport failed"));
    expect(await writing).toBe(false);
    expect(await reading).toBe(true);
    expect(next.sinks.busy).toHaveBeenLastCalledWith(false);
    expect(next.sinks.goals).toHaveBeenCalledWith([personalGoal]);
    expect(next.sinks.operation).toHaveBeenLastCalledWith({ phase: "unconfirmed", goalId: personalGoal.id, checked: true });
    expect(run).toHaveBeenCalledTimes(1);
    expect(first.sinks.error).not.toHaveBeenCalledWith("transport failed");
    next.actions.dispose();
  });

  it("keeps an unconfirmed submission visible after reading and never retries it automatically", async () => {
    const commit = deferred<PersonalGoal>();
    const list = vi.fn(async () => [personalGoal]);
    const state = setup(list), run = vi.fn(() => commit.promise);
    const writing = state.actions.write(run, personalGoal.id);
    expect(state.sinks.operation).toHaveBeenLastCalledWith({ phase: "submitting", goalId: personalGoal.id });
    commit.reject(new Error("response lost"));
    expect(await writing).toBe(false);
    expect(state.sinks.operation).toHaveBeenLastCalledWith({ phase: "unconfirmed", goalId: personalGoal.id, checked: false });
    expect(await state.actions.write(run, personalGoal.id)).toBe(false);
    expect(run).toHaveBeenCalledTimes(1);
    list.mockRejectedValueOnce(new Error("read offline"));
    expect(await state.actions.refresh()).toBe(false);
    expect(state.sinks.operation).toHaveBeenLastCalledWith({ phase: "unconfirmed", goalId: personalGoal.id, checked: false });
    expect(await state.actions.refresh()).toBe(true);
    expect(state.sinks.operation).toHaveBeenLastCalledWith({ phase: "unconfirmed", goalId: personalGoal.id, checked: true });
    expect(run).toHaveBeenCalledTimes(1);
    const explicitRetry = vi.fn(async () => personalGoal);
    expect(await state.actions.write(explicitRetry, personalGoal.id)).toBe(true);
    expect(explicitRetry).toHaveBeenCalledTimes(1);
    expect(state.sinks.operation).toHaveBeenLastCalledWith({ phase: "idle" });
    state.actions.dispose();
  });

  it("a reopened panel retains the missing-receipt notice while another client stays independent", async () => {
    const first = setup();
    expect(await first.actions.write(async () => { throw new Error("unconfirmed"); }, personalGoal.id)).toBe(false);
    first.actions.dispose();
    const next = reopen(first.client);
    expect(await next.actions.refresh()).toBe(true);
    expect(next.sinks.operation.mock.calls).toEqual([
      [{ phase: "unconfirmed", goalId: personalGoal.id, checked: false }], [{ phase: "unconfirmed", goalId: personalGoal.id, checked: true }],
    ]);
    const other = setup();
    expect(await other.actions.refresh()).toBe(true);
    expect(other.sinks.operation).toHaveBeenLastCalledWith({ phase: "idle" });
    next.actions.dispose(); other.actions.dispose();
  });

  it("an invalid receipt is unconfirmed and a valid receipt is accepted only until read-back", async () => {
    const malformed = setup();
    expect(await malformed.actions.write(async () => ({ ...personalGoal, id: "personal-other" }), personalGoal.id)).toBe(false);
    expect(malformed.sinks.operation).toHaveBeenLastCalledWith({ phase: "unconfirmed", goalId: personalGoal.id, checked: false });
    malformed.actions.dispose();
    const readBack = deferred<PersonalGoal[]>();
    const state = setup(vi.fn(() => readBack.promise));
    const writing = state.actions.write(async () => personalGoal, personalGoal.id);
    await Promise.resolve();
    expect(state.sinks.operation).toHaveBeenLastCalledWith({ phase: "accepted", goalId: personalGoal.id });
    readBack.resolve([personalGoal]);
    expect(await writing).toBe(true);
    expect(state.sinks.operation).toHaveBeenLastCalledWith({ phase: "idle" });
    state.actions.dispose();
  });

  it.each(["date", "progress"])("a malformed %s in read-back cannot clear an accepted version", async fault => {
    const updated = { ...personalGoal, revision: 2 };
    const invalid = fault === "date" ? { ...updated, due_at: "broken" } : { ...updated, progress: { ...updated.progress, passed: 99 } };
    const list = vi.fn(async () => [invalid]);
    const state = setup(list);
    expect(await state.actions.write(async () => updated, personalGoal.id)).toBe(false);
    expect(state.sinks.goals).not.toHaveBeenCalled();
    expect(state.sinks.operation).toHaveBeenLastCalledWith({ phase: "accepted", goalId: personalGoal.id });
    list.mockResolvedValue([updated]);
    expect(await state.actions.refresh()).toBe(true);
    expect(state.sinks.goals).toHaveBeenCalledWith([updated]);
    expect(state.sinks.operation).toHaveBeenLastCalledWith({ phase: "idle" });
    state.actions.dispose();
  });

  it("keeps the submitted goal identity across reopening and differently ordered lists", async () => {
    const other = { ...personalGoal, id: "personal-other", objective: "另一目标" };
    const first = setup(vi.fn(async () => [other, personalGoal]));
    expect(await first.actions.write(async () => { throw new Error("response lost"); }, personalGoal.id)).toBe(false);
    first.actions.dispose();
    const next = reopen(first.client);
    expect(await next.actions.refresh()).toBe(true);
    expect(next.sinks.operation).toHaveBeenLastCalledWith({ phase: "unconfirmed", goalId: personalGoal.id, checked: true });
    const commit = deferred<PersonalGoal>();
    const explicitWrite = next.actions.write(() => commit.promise, other.id);
    expect(next.sinks.operation).toHaveBeenLastCalledWith({ phase: "submitting", goalId: other.id });
    commit.resolve(other);
    expect(await explicitWrite).toBe(true);
    expect(next.sinks.operation).toHaveBeenLastCalledWith({ phase: "idle" });
    next.actions.dispose();
  });

  it("preserves absolute instants when editing local date fields", () => {
    const stamp = "2026-10-18T09:15:00.000Z";
    expect(goalDate(localGoalDate(stamp))).toBe(stamp);
    expect(goalDate("")).toBe("");
    expect(localGoalDate("")).toBe("");
  });
});
