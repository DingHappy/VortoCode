import { expect, it, vi } from "vitest";
import { decideStaffAction, type StaffAction, type StaffActionClient } from "./staffAction";
import { taskFeedItem } from "./activityFeed";
import { assistantAttention } from "./assistantAttention";
import type { TaskItem } from "../types";

export function fixtureCard(): StaffAction {
  return { digest: "d".repeat(64), status: "pending", reason: "", decision: null, payload: {
    id: "staff-action-" + "a".repeat(32), task_id: "task-0123456789", round: 1,
    member: "staff-helper", member_revision: 1, role: "organizer", role_revision: 1,
    instance: "b".repeat(32), runtime: "runtime", nonce: "e".repeat(32),
    tool: "personal_goal_create", args: { objective: "列出学习书单" }, kind: "own_data",
    message: "新建个人目标：列出学习书单", tainted: false, approval_level: "normal", expires_at: 2000000000 } };
}
function fixture(card = fixtureCard()) {
  const client: StaffActionClient = { readStaffAction: vi.fn(async () => structuredClone(card)),
    decideStaffAction: vi.fn(async (_t, _a, approve) => ({ status: approve ? "approved" : "denied", replayed: false })) };
  return { card, client };
}
it.each(["normal", "desktop"] as const)("%s cards use explicit owner confirmation with full binding", async level => {
  const { card, client } = fixture(); card.payload.approval_level = level;
  const binding = { digest: card.digest, nonce: card.payload.nonce, round: 1, instance: card.payload.instance, runtime: card.payload.runtime };
  await decideStaffAction(client, card, true, "", () => true);
  expect(client.decideStaffAction).toHaveBeenCalledWith(card.payload.task_id, card.payload.id, true, binding, "");
  await decideStaffAction(client, card, false, "  只要清单，不要保存  ", () => true);
  expect(client.decideStaffAction).toHaveBeenLastCalledWith(card.payload.task_id, card.payload.id, false, binding, "只要清单，不要保存");
  await expect(decideStaffAction(client, card, false, "", () => true)).rejects.toThrow("理由");
});
it.each(["status", "digest", "round", "id", "task_id", "nonce", "instance", "runtime"])("refuses a changed %s before sending", async field => {
  const { card, client } = fixture(); const fresh = structuredClone(card);
  if (field === "status") fresh.status = "interrupted";
  else if (field === "digest") fresh.digest = "changed";
  else if (field === "round") fresh.payload.round = 2;
  else (fresh.payload as unknown as Record<string, unknown>)[field] = "changed";
  vi.mocked(client.readStaffAction).mockResolvedValue(fresh);
  await expect(decideStaffAction(client, card, true, "", () => true)).rejects.toThrow("变化或失效");
  expect(client.decideStaffAction).not.toHaveBeenCalled();
});
it("a changed connection while reading cannot send a late decision", async () => {
  const { card, client } = fixture(); let active = true;
  vi.mocked(client.readStaffAction).mockImplementation(async () => { active = false; return card; });
  await decideStaffAction(client, card, true, "", () => active);
  expect(client.decideStaffAction).not.toHaveBeenCalled();
});
it("projects exactly one owner attention item, and removes it as soon as the decision is saved", () => {
  const task: TaskItem = { id: "task-0123456789", kind: "delegation", status: "blocked", prompt: "整理学习计划",
    collaboration: { round: 1, assignee: "staff-helper", review: "not_submitted", acceptance: [], tainted: false, messages: [],
      dispatch: { source: "staff", max_steps: 12, timeout_seconds: 600 }, approval: fixtureCard() } };
  const item = taskFeedItem(task);
  expect(item.status).toBe("waiting"); expect(item.summary).toContain("专员等你批准");
  expect(assistantAttention([item]).decisions).toHaveLength(1);
  task.collaboration!.approval!.status = "approved";
  expect(assistantAttention([taskFeedItem(task)]).decisions).toHaveLength(0);
});
