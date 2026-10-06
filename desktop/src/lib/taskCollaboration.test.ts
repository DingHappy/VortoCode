import { describe, expect, it } from "vitest";
import type { TaskItem } from "../types";
import { taskCollaborationDraft } from "./taskCollaboration";

const task: TaskItem = {
  id: "task-abc", kind: "delegation", status: "done", owner_session: "sid-parent",
  collaboration: { assignee: "reader", round: 2, review: "pending", acceptance: [], tainted: false, messages: [] },
};

describe("task collaboration drafts", () => {
  it("routes review to the owner and includes the task and exact round", () => {
    const draft = taskCollaborationDraft(task, "review");
    expect(draft?.sid).toBe("parent");
    expect(draft?.text).toContain("task-abc 第 2 轮");
    expect(draft?.text).toContain("task_status");
    expect(draft?.text).toContain("task_review");
  });

  it("only prepares feedback for unfinished acceptance within the retry budget", () => {
    expect(taskCollaborationDraft(task, "followup")?.text).toContain("补充要求：");
    const accepted = { ...task, collaboration: { ...task.collaboration!, review: "accepted" as const } };
    expect(taskCollaborationDraft(accepted, "review")).toBeNull();
    expect(taskCollaborationDraft(accepted, "followup")).toBeNull();
    expect(taskCollaborationDraft({ ...task, collaboration: { ...task.collaboration!, round: 3 } }, "followup")).toBeNull();
    expect(taskCollaborationDraft({ ...task, status: "running" }, "followup")).toBeNull();
    expect(taskCollaborationDraft({ ...task, status: "failed" }, "review")).toBeNull();
    expect(taskCollaborationDraft({ ...task, status: "failed" }, "followup")).not.toBeNull();
  });

  it("rejects unknown ownership, malformed identifiers and legacy tasks", () => {
    for (const altered of [
      { ...task, kind: "dev" }, { ...task, id: "../task-abc" },
      { ...task, owner_session: "agent-random" }, { ...task, owner_session: "sid-../other" },
      { ...task, collaboration: { ...task.collaboration!, round: Number.NaN } },
    ]) expect(taskCollaborationDraft(altered, "review")).toBeNull();
  });

  it("requires a fresh submission after dependency failure before any results were consumed", () => {
    const failed = { ...task, status: "failed", dependencies: { requires: [{ task_id: "task-parent", round: 1 }], resolution: "failed" as const } };
    expect(taskCollaborationDraft(failed, "followup")).toBeNull();
    expect(taskCollaborationDraft({ ...failed, dependencies: { ...failed.dependencies, resolution: "consumed" } }, "followup")).not.toBeNull();
    expect(taskCollaborationDraft({ ...task, dependencies: {} }, "followup")).not.toBeNull();
    expect(taskCollaborationDraft({ ...failed, dependencies: { failure: "依赖合同损坏" } }, "followup")).toBeNull();
  });
});
