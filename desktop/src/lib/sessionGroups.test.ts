import { describe, expect, it } from "vitest";

import type { SessionSummary } from "../types";
import { groupSessions, sessionGroupKey } from "./sessionGroups";

const session = (sid: string, patch: Partial<SessionSummary> = {}): SessionSummary =>
  ({ sid, title: sid, messages: 1, updated: 0, status: "idle", ...patch });

describe("groupSessions", () => {
  it("puts sessions waiting on a person first, then running ones, keeping order inside groups", () => {
    const groups = groupSessions([
      session("a"),
      session("b", { status: "working" }),
      session("c", { status: "needs_input" }),
      session("d", { background_tasks: { total: 1, active: 0, attention: 1, completed: 0, latest_status: "", latest_prompt: "",
        branch: "", latest_task_id: "", active_task_id: "", attention_task_id: "", plan_id: "", worktree_count: 0 } }),
      session("e", { status: "queued" }),
      session("f", { status: "completed" }),
    ], "", false);
    expect(groups.map((group) => [group.key, group.sessions.map((item) => item.sid)])).toEqual([
      ["needs", ["c", "d"]], ["working", ["b", "e"]], ["other", ["a", "f"]],
    ]);
  });

  it("drops empty groups so a quiet list looks like before", () => {
    expect(groupSessions([session("a"), session("b")], "", false).map((group) => group.key)).toEqual(["other"]);
  });

  it("treats the active session as working while it is locally busy, unless it is waiting for input", () => {
    expect(sessionGroupKey(session("a"), true, true)).toBe("working");
    expect(sessionGroupKey(session("a", { status: "needs_input" }), true, true)).toBe("needs");
    expect(sessionGroupKey(session("a"), false, true)).toBe("other");
    expect(sessionGroupKey(session("a", { status: "failed" }), false, false)).toBe("needs");
    expect(sessionGroupKey(session("a", { hook_issues: { count: 1, items: [], truncated: false } }), false, false)).toBe("needs");
  });
});
