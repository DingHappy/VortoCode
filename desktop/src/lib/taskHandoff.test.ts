import { describe, expect, it } from "vitest";
import type { TaskItem } from "../types";
import { taskHandoffIdentity } from "./taskHandoff";

const revision = "a".repeat(64);
function task(): TaskItem {
  return { id: "task-result", kind: "dev", status: "done", owner_session: "sid-owner",
    handoff: { completed: [], remaining: [], next_action: "汇报", text: "结果",
      handling: { revision, handled: false, handled_at: "" } } };
}

describe("manual task handoff identity", () => {
  it.each(["done", "failed", "cancelled", "paused", "interrupted"])("allows owned unread %s results", (status) => {
    expect(taskHandoffIdentity({ ...task(), status })).toEqual({ session: "owner", revision });
  });

  it("allows API delegation independently of its review verdict", () => {
    const item = task();
    item.kind = "delegation";
    item.collaboration = { assignee: "reader", round: 2, review: "accepted", acceptance: [],
      tainted: true, messages: [], dispatch: { source: "api", max_steps: 4, timeout_seconds: 300 } };
    expect(taskHandoffIdentity(item)).toEqual({ session: "owner", revision });
    delete item.collaboration.dispatch;
    expect(taskHandoffIdentity(item)).toBeNull();
  });

  it("allows the resumed development result to be handled independently of its parent", () => {
    const item = { ...task(), id: "task-resume-result", kind: "dev-resume", parent_task_id: "task-original" };
    expect(taskHandoffIdentity(item)).toEqual({ session: "owner", revision });
    item.status = "queued";
    expect(taskHandoffIdentity(item)).toBeNull();
  });

  it.each([
    { id: "task-bad/../path" }, { id: "task-result\n" }, { owner_session: "sid-" },
    { owner_session: "sid-owner\n" }, { owner_session: "owner" }, { status: "running" },
    { status: "queued" }, { kind: "command" }, { handoff: undefined },
  ])("rejects missing or ineligible server identity %j", (override) => {
    expect(taskHandoffIdentity({ ...task(), ...override })).toBeNull();
  });

  it("does not infer unread state or repair a bad revision in the client", () => {
    const item = task();
    item.handoff!.handling!.handled = true;
    expect(taskHandoffIdentity(item)).toBeNull();
    item.handoff!.handling!.handled = false;
    for (const invalid of ["", "revision", "A".repeat(64), revision + "\n"]) {
      item.handoff!.handling!.revision = invalid;
      expect(taskHandoffIdentity(item)).toBeNull();
    }
  });
});
