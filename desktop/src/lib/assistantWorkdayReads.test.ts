import { expect, it, vi } from "vitest";
import type { CodexProgressSnapshot, PersonalGoal } from "../types";
import { assistantWorkdayReads, startWorkdayClock } from "./assistantWorkdayReads";
import { freshAt } from "./readFreshness";
const personal: PersonalGoal = { id: "personal-example", kind: "personal", revision: 1, objective: "Release", status: "achieved",
  constraints: [], non_goals: [], task_ids: [], due_at: "", follow_up_at: "", overdue: false, follow_up_due: false,
  progress: { passed: 1, failed: 0, total: 1 },
  acceptance_criteria: [{ id: "c1", text: "Check release", status: "passed", evidence_ids: ["e1"] }],
  evidence: [{ id: "e1", criterion_id: "c1", kind: "personal_acceptance", source: "user_confirmation", summary: "Checked", passed: true }] };

it("expires an idle read and catches clock changes on visibility resume, with cleanup", async () => {
  vi.useFakeTimers();
  const start = new Date("2026-10-24T12:00:00Z");
  vi.setSystemTime(start);
  let visibilityListener: (() => void) | undefined;
  const visibility = { hidden: false,
    addEventListener: vi.fn((_name, listener) => { visibilityListener = listener as () => void; }),
    removeEventListener: vi.fn(),
  };
  const update = vi.fn();
  const stop = startWorkdayClock(update, visibility);
  try {
    await vi.advanceTimersByTimeAsync(0);
    expect(freshAt(start.getTime(), update.mock.lastCall![0].getTime())).toBe(true);
    await vi.advanceTimersByTimeAsync(121_000);
    expect(freshAt(start.getTime(), update.mock.lastCall![0].getTime())).toBe(false);
    vi.setSystemTime(new Date(start.getTime() - 1));
    visibilityListener!();
    await vi.advanceTimersByTimeAsync(0);
    expect(freshAt(start.getTime(), update.mock.lastCall![0].getTime())).toBe(false);
    stop();
    const calls = update.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(update).toHaveBeenCalledTimes(calls);
    expect(visibility.removeEventListener).toHaveBeenCalledWith("visibilitychange", visibilityListener);
  } finally { stop(); vi.useRealTimers(); }
});
const deferred = <T,>() => {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
function client() {
  return { listPersonalGoals: vi.fn(async () => [] as PersonalGoal[]),
    getCodexProgress: vi.fn(async () => ({ overview: "", threads: [] } as CodexProgressSnapshot)) };
}
function harness() {
  const state = { client: client(), current: true };
  const goals = vi.fn(), codex = vi.fn();
  const reads = assistantWorkdayReads({ client: () => state.client, current: () => state.current, goals, codex, now: () => 100 });
  return { state, goals, codex, reads };
}
it("publishes available sources while another source is still loading", async () => {
  const h = harness(), slow = deferred<PersonalGoal[]>();
  h.state.client.listPersonalGoals.mockReturnValueOnce(slow.promise);
  const pending = h.reads.refresh(); await Promise.resolve();
  expect(h.codex).toHaveBeenCalledWith({ status: "current", data: { overview: "", threads: [] }, checkedAt: 100 });
  expect(h.goals).not.toHaveBeenCalled();
  slow.resolve([]); await pending;
});
it("coalesces manual and periodic reads on the same host", async () => {
  const h = harness(), slow = deferred<PersonalGoal[]>();
  h.state.client.listPersonalGoals.mockReturnValueOnce(slow.promise);
  const first = h.reads.refresh(), second = h.reads.refresh();
  expect(first).toBe(second); expect(h.state.client.listPersonalGoals).toHaveBeenCalledTimes(1);
  slow.resolve([]); await first;
});
it("marks failures unavailable while retaining historical records only for that host", async () => {
  const h = harness();
  h.state.client.getCodexProgress.mockResolvedValueOnce({ overview: "old", threads: [] });
  await h.reads.refresh();
  h.state.client.getCodexProgress.mockRejectedValueOnce(new Error("offline"));
  await h.reads.refresh();
  expect(h.codex).toHaveBeenLastCalledWith({ status: "unavailable", data: { overview: "old", threads: [] }, checkedAt: 100 });
  expect(h.goals).toHaveBeenLastCalledWith({ status: "current", data: [], checkedAt: 100 });
});
it("drops all late responses after unmount or scope invalidation", async () => {
  const h = harness(), slow = deferred<PersonalGoal[]>();
  h.state.client.listPersonalGoals.mockReturnValueOnce(slow.promise);
  const pending = h.reads.refresh(); h.reads.invalidate(); h.state.current = false;
  slow.resolve([]); await pending;
  expect(h.goals).not.toHaveBeenCalled(); expect(h.codex).not.toHaveBeenCalled();
});
it("a new host starts independent reads and never inherits another host's history on failure", async () => {
  const h = harness(), slow = deferred<PersonalGoal[]>(), old = h.state.client;
  old.listPersonalGoals.mockReturnValueOnce(slow.promise);
  const pending = h.reads.refresh();
  h.state.client = client(); h.state.client.getCodexProgress.mockRejectedValueOnce(new Error("new host offline"));
  await h.reads.refresh(); slow.resolve([{ id: "old" }] as PersonalGoal[]); await pending;
  expect(h.goals).toHaveBeenCalledTimes(1);
  expect(h.codex).toHaveBeenLastCalledWith({ status: "unavailable", data: null });
});
it("malformed snapshots are unknown rather than valid empty snapshots", async () => {
  const h = harness();
  h.state.client.listPersonalGoals.mockResolvedValueOnce([{ id: "invalid" }] as PersonalGoal[]);
  h.state.client.getCodexProgress.mockResolvedValueOnce({ threads: [{ id: "x" }] } as CodexProgressSnapshot);
  await h.reads.refresh();
  expect(h.goals).toHaveBeenLastCalledWith({ status: "unavailable", data: [] });
  expect(h.codex).toHaveBeenLastCalledWith({ status: "unavailable", data: null });
});
it("retains per-thread unavailable observations without discarding the whole source", async () => {
  const h = harness();
  const data = { overview: "partial", threads: [
    { id: "th1", name: "Unavailable", status: "active", flags: [], execution: null },
    { id: "th2", name: "Legacy", status: "idle", flags: [] },
  ] };
  h.state.client.getCodexProgress.mockResolvedValueOnce(data);
  await h.reads.refresh();
  expect(h.codex).toHaveBeenLastCalledWith({ status: "current", data, checkedAt: 100 });
});

it("rejects a malformed goal refresh while retaining history and continuing to publish Codex", async () => {
  const h = harness();
  h.state.client.listPersonalGoals.mockResolvedValueOnce([personal]);
  await h.reads.refresh();
  h.state.client.listPersonalGoals.mockResolvedValueOnce([{ ...personal, due_at: "broken" }]);
  await h.reads.refresh();
  expect(h.goals).toHaveBeenLastCalledWith({ status: "unavailable", data: [personal], checkedAt: 100 });
  expect(h.codex).toHaveBeenLastCalledWith(expect.objectContaining({ status: "current" }));
  h.state.client.listPersonalGoals.mockResolvedValueOnce([personal]);
  await h.reads.refresh();
  expect(h.goals).toHaveBeenLastCalledWith({ status: "current", data: [personal], checkedAt: 100 });
});

it.each(["goal id", "criterion id", "evidence reference", "evidence id", "duplicate goals"])(
  "rejects goal snapshots with unusable acceptance identities: %s", async fault => {
    const h = harness();
    h.state.client.listPersonalGoals.mockResolvedValueOnce([personal]);
    await h.reads.refresh();
    expect(h.goals).toHaveBeenLastCalledWith({ status: "current", data: [personal], checkedAt: 100 });
    const goals = [structuredClone(personal)];
    if (fault === "goal id") goals[0].id = " ";
    if (fault === "criterion id") goals[0].acceptance_criteria[0].id = "";
    if (fault === "evidence reference") goals[0].acceptance_criteria[0].evidence_ids = [""];
    if (fault === "evidence id") goals[0].evidence[0].id = "";
    if (fault === "duplicate goals") goals.push({ ...goals[0] });
    h.state.client.listPersonalGoals.mockResolvedValueOnce(goals as PersonalGoal[]);
    await h.reads.refresh();
    expect(h.goals).toHaveBeenLastCalledWith({ status: "unavailable", data: [personal], checkedAt: 100 });
  });

it("reads schedule and memory proposals alongside goals; an older server without them is unavailable, not empty", async () => {
  const base = { listPersonalGoals: async () => [] as PersonalGoal[], getCodexProgress: async () => ({ overview: "", received_at: "", threads: [] }) as CodexProgressSnapshot };
  const seen: Record<string, unknown> = {};
  const reads = assistantWorkdayReads({ client: () => client, current: () => true, now: () => 5,
    goals: () => {}, codex: () => {}, schedule: source => { seen.schedule = source; }, proposals: source => { seen.proposals = source; } });
  let client: Parameters<typeof assistantWorkdayReads>[0]["client"] extends () => infer C ? C : never = {
    ...base,
    getAssistantSchedule: async () => [{ id: "j", kind: "cron", title: "早报", schedule: "", enabled: true, last_status: "failed" }],
    listMemoryProposals: async () => [{ id: "m", content: "记住", created_at: "", tainted: false }],
  };
  await reads.refresh();
  expect(seen.schedule).toMatchObject({ status: "current", checkedAt: 5, data: [{ id: "j" }] });
  expect(seen.proposals).toMatchObject({ status: "current", data: [{ id: "m" }] });
  client = { ...base };
  await reads.refresh();
  expect(seen.schedule).toMatchObject({ status: "unavailable" });
  expect(seen.proposals).toMatchObject({ status: "unavailable" });
});
