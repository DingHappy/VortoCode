import { expect, it, vi } from "vitest";
import { decideStaffAction, signStaffAction, type StaffAction, type StaffActionClient } from "./staffAction";
import { taskFeedItem } from "./activityFeed";
import { assistantAttention } from "./assistantAttention";
import type { TaskItem } from "../types";
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
import { invoke } from "@tauri-apps/api/core";

export function fixtureCard(): StaffAction {
  return { digest: "d".repeat(64), status: "pending", reason: "", decision: null, payload: {
    id: "staff-action-" + "a".repeat(32), task_id: "task-0123456789", round: 1,
    member: "staff-helper", member_revision: 1, role: "organizer", role_revision: 1,
    instance: "b".repeat(32), key_id: "c".repeat(64), nonce: "e".repeat(32),
    tool: "personal_goal_create", args: { objective: "列出学习书单" }, kind: "own_data",
    message: "新建个人目标：列出学习书单", tainted: false, required_key: "device", expires_at: 2000000000 } };
}
function fixture(card = fixtureCard()) {
  const client: StaffActionClient = { readStaffAction: vi.fn(async () => structuredClone(card)),
    decideStaffAction: vi.fn(async (_t, _a, approve) => ({ status: approve ? "approved" : "denied", replayed: false })) };
  return { card, client };
}
it("reads a fresh exact card before approval and passes rejection reason to the owner API", async () => {
  const { card, client } = fixture(); const sign = vi.fn();
  await decideStaffAction(client, card, true, "", () => true, sign);
  expect(client.decideStaffAction).toHaveBeenCalledWith(card.payload.task_id, card.payload.id, true, card.digest, "", undefined);
  expect(sign).not.toHaveBeenCalled();
  await decideStaffAction(client, card, false, "  只要清单，不要保存  ", () => true, sign);
  expect(client.decideStaffAction).toHaveBeenLastCalledWith(card.payload.task_id, card.payload.id, false, card.digest, "只要清单，不要保存", undefined);
  await expect(decideStaffAction(client, card, false, "", () => true, sign)).rejects.toThrow("理由");
});
it.each(["status", "digest", "round", "id", "task_id"])("refuses a changed %s before native signing or sending", async field => {
  const { card, client } = fixture(); const fresh = structuredClone(card), sign = vi.fn();
  if (field === "status") fresh.status = "interrupted";
  else if (field === "digest") fresh.digest = "changed";
  else if (field === "round") fresh.payload.round = 2;
  else if (field === "id") fresh.payload.id = "another";
  else fresh.payload.task_id = "another-task";
  vi.mocked(client.readStaffAction).mockResolvedValue(fresh);
  await expect(decideStaffAction(client, card, true, "", () => true, sign)).rejects.toThrow("变化或失效");
  expect(sign).not.toHaveBeenCalled(); expect(client.decideStaffAction).not.toHaveBeenCalled();
});
it("requires a native proof for high risk and refuses cancelled Touch ID without fallback", async () => {
  const { card, client } = fixture(); card.payload.required_key = "approval";
  const sign = vi.fn().mockRejectedValue(new Error("识别取消"));
  await expect(decideStaffAction(client, card, true, "", () => true, sign)).rejects.toThrow("取消");
  expect(client.decideStaffAction).not.toHaveBeenCalled();
  sign.mockResolvedValue({ public_key: "public", signature: "proof" });
  await decideStaffAction(client, card, true, "", () => true, sign);
  expect(client.decideStaffAction).toHaveBeenCalledWith(card.payload.task_id, card.payload.id, true, card.digest, "", "proof");
});
it("disposal or a changed connection during the native wait cannot send a late decision", async () => {
  const { card, client } = fixture(); card.payload.required_key = "approval";
  let active = true;
  const sign = vi.fn(async () => { active = false; return { public_key: "public", signature: "proof" }; });
  await decideStaffAction(client, card, true, "", () => active, sign);
  expect(client.decideStaffAction).not.toHaveBeenCalled();
});
it("the native command receives structured identifiers, never an arbitrary message", async () => {
  const card = fixtureCard(); vi.mocked(invoke).mockResolvedValue({ public_key: "public", signature: "proof" });
  await signStaffAction(card);
  expect(invoke).toHaveBeenCalledWith("staff_action_sign", { instance: card.payload.instance, keyId: card.payload.key_id,
    taskId: card.payload.task_id, round: 1, actionId: card.payload.id, digest: card.digest, nonce: card.payload.nonce });
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
