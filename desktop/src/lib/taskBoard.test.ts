import { describe, expect, it } from "vitest";
import { boardIdentity, buildTaskBoard } from "./taskBoard";
import type { DesktopRuntimeInbox, RuntimeInboxTask } from "../types";

function source(runtimeId = "a"): DesktopRuntimeInbox {
  return { runtimeId, label: runtimeId, scope: "project", baseUrl: "", error: "", checkedAt: 1,
    snapshot: { version: 1, scope: "project", generated_at: "now", sessions: [], decisions: [], goals: [], tasks: [], counts: {} as never } };
}
function task(id: string, status: string, owner = "owner", goal = "goal"): RuntimeInboxTask {
  return { id, status, prompt: id, owner_session: owner, goal_id: goal, detail: "", plan_id: "", branch: "", updated: "" };
}

describe("Desktop bounded task board", () => {
  it("never promotes done to acceptance or Goal achievement; cancellation stays inspectable", () => {
    const a = source();
    a.snapshot!.tasks = [task("done", "done"), task("cancelled", "cancelled"), task("wait", "waiting"), task("run", "running"), task("queue", "queued")];
    expect(buildTaskBoard([a]).map((item) => [item.id, item.column])).toEqual([
      ["done", "attention"], ["cancelled", "attention"], ["wait", "attention"], ["run", "working"], ["queue", "queued"],
    ]);
    expect(buildTaskBoard([a])[0].detail).toContain("结果有效性");
  });
  it("scopes identical task/session/Goal IDs by runtime and preserves original owner", () => {
    const a = source("a"), b = source("b");
    a.snapshot!.tasks = [task("same", "running")]; b.snapshot!.tasks = [task("same", "done")];
    const all = buildTaskBoard([a, b]);
    expect(new Set(all.map((item) => item.key)).size).toBe(2);
    expect(buildTaskBoard([a, b], { sessionKey: boardIdentity("a", "owner") })).toEqual([all[0]]);
    expect(buildTaskBoard([a, b], { goalKey: boardIdentity("b", "goal") })).toEqual([all[1]]);
    expect(all[1].source).toBe(b); expect(all[1].sessionId).toBe("owner");
    expect(buildTaskBoard([a, b], { runtimeId: "a", goalKey: boardIdentity("b", "goal") })).toEqual([]);
  });
  it("retains stale evidence and does not fabricate work for a missing snapshot", () => {
    const a = source(); a.snapshot!.tasks = [task("same", "running")]; a.error = "offline";
    const b = source("b"); b.snapshot = null;
    expect(buildTaskBoard([a, b])).toHaveLength(1);
    expect(buildTaskBoard([a, b])[0].source.error).toBe("offline");
    expect(buildTaskBoard([a], { sessionKey: boardIdentity("a", "removed") })).toEqual([]);
  });
  it("Goal achievement comes only from the authoritative Goal state, not task counts", () => {
    const a = source(); a.snapshot!.tasks = [task("done", "done")];
    a.snapshot!.goals = ["active", "blocked", "draft", "achieved"].map((status) => ({ id: status,
      objective: status, status, blocker: "", next_action: "", updated: "", progress: { passed: 0, failed: 0, total: 3 } }));
    expect(buildTaskBoard([a]).filter((item) => item.column === "completed").map((item) => item.id)).toEqual(["achieved"]);
    expect(buildTaskBoard([a])[0].detail).toContain("0/3");
  });
  it("does not infer session membership for project goals; filters use intersection", () => {
    const a = source(); a.snapshot!.tasks = [task("task", "done")];
    a.snapshot!.goals = [{ id: "goal", objective: "goal", status: "active", blocker: "", next_action: "", updated: "", progress: { passed: 1, failed: 0, total: 2 } }];
    expect(buildTaskBoard([a], { sessionKey: boardIdentity("a", "owner"), goalKey: boardIdentity("a", "goal") }).map((item) => item.kind)).toEqual(["task"]);
  });
  it("unsupported snapshot versions cannot masquerade as current coverage", () => {
    const a = source(); a.snapshot!.tasks = [task("done", "done")]; a.snapshot!.version = 2;
    expect(buildTaskBoard([a])).toEqual([]);
  });
  it("keeps confirmations in attention and binds Goal decisions to the source Goal", () => {
    const a = source(); a.snapshot!.decisions = [{ id: "decision", kind: "goal", severity: "high", title: "阻塞", detail: "", target_id: "goal", action: "open_goal", session_id: "owner", can_dismiss: false }];
    expect(buildTaskBoard([a], { goalKey: boardIdentity("a", "goal") })[0]).toMatchObject({kind: "decision", column: "attention", sessionId: "owner", goalId: "goal"});
  });
  it("does not mutate source ledgers while grouping", () => {
    const a = source(); a.snapshot!.tasks = [task("done", "done")]; const before = JSON.stringify(a);
    buildTaskBoard([a]); expect(JSON.stringify(a)).toBe(before);
  });
});

import { boardSessionId, validTaskLifecycle } from "./taskBoard";
import type { TaskLifecycleSummary } from "../types";
const lifecycle = (changes: Partial<TaskLifecycleSummary> = {}): TaskLifecycleSummary => ({
  version: 1, execution: "done", kind: "delegation", review: "accepted", handling: "handled",
  validity: "not_applicable", round: 1, revision: "a".repeat(64), column: "completed", label: "已验收 · 交接已处理", recovered_by: "", ...changes,
});
it("consumes current server lifecycle and normalizes exactly one owner prefix", () => {
  const a = source(); const value = { ...task("task", "done", "sid-owner"), lifecycle: lifecycle() };
  a.snapshot!.tasks = [value];
  expect(buildTaskBoard([a], { sessionKey: boardIdentity("a", "owner") })[0]).toMatchObject({ column: "completed", sessionId: "owner" });
  expect(boardSessionId("sid-sid-owner")).toBe("sid-owner");
  expect(value.owner_session).toBe("sid-owner");
});
it.each([
  { version: 2 }, { execution: "running" }, { validity: "unknown" }, { validity: "invalid" },
  { review: "pending" }, { handling: "pending" }, { revision: "invalid" }, { column: "history" },
])("fails closed for contradictory or unverifiable completed summaries %j", (change) => {
  const a = source(); const value = { ...task("task", "done"), lifecycle: lifecycle(change as never) };
  a.snapshot!.tasks = [value];
  expect(validTaskLifecycle(value)).toBeUndefined(); expect(buildTaskBoard([a])[0].column).toBe("attention");
});
it("does not label dev handled as accepted and retains recovered history", () => {
  const a = source(); a.snapshot!.tasks = [
    { ...task("dev", "done"), lifecycle: lifecycle({kind: "dev", review: "not_applicable", round: null, label: "执行结束 · 交接已处理（不代表验收）"}) },
    { ...task("old", "interrupted"), lifecycle: lifecycle({kind: "dev", execution: "interrupted", review: "not_applicable", round: null, handling: "pending", column: "history", label: "已由恢复任务接续", recovered_by: "task-new"}) },
  ];
  expect(buildTaskBoard([a]).map((item) => item.column)).toEqual(["completed", "history"]);
  expect(buildTaskBoard([a])[0].lifecycle?.review).toBe("not_applicable");
});
