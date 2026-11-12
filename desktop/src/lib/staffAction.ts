import type { StaffConfirmation } from "./staff";

export interface StaffActionConfirmation extends StaffConfirmation { runtime: string }

export interface StaffAction {
  digest: string;
  status: "pending" | "approved" | "denied" | "cancelled" | "expired" | "interrupted";
  reason: string;
  decision: boolean | null;
  payload: {
    id: string; task_id: string; round: number; member: string; member_revision: number;
    member_name?: string; role_title?: string;
    role: string; role_revision: number; instance: string; runtime: string; nonce: string;
    tool: string; args: Record<string, unknown>; kind: string; message: string;
    tainted: boolean; approval_level: "normal" | "desktop"; expires_at: number;
  };
}
export interface StaffActionClient {
  readStaffAction(task: string): Promise<StaffAction>;
  decideStaffAction(task: string, action: string, approve: boolean, confirmation: StaffActionConfirmation,
    reason: string): Promise<{ status: string; replayed: boolean }>;
}
/** Changed identities or a disconnected client cannot send a decision. */
export async function decideStaffAction(client: StaffActionClient, expected: StaffAction, approve: boolean,
  reason: string, isCurrent: () => boolean) {
  if (!approve && !reason.trim()) throw new Error("请填写拒绝理由，专员需要据此调整做法");
  const card = await client.readStaffAction(expected.payload.task_id);
  if (!isCurrent()) return;
  if (card.status !== "pending" || card.digest !== expected.digest || card.payload.id !== expected.payload.id
    || card.payload.task_id !== expected.payload.task_id || card.payload.round !== expected.payload.round
    || card.payload.nonce !== expected.payload.nonce || card.payload.instance !== expected.payload.instance
    || card.payload.runtime !== expected.payload.runtime)
    throw new Error("审批卡已变化或失效，请刷新后核对");
  if (!isCurrent()) return;
  const result = await client.decideStaffAction(card.payload.task_id, card.payload.id, approve,
    { digest: card.digest, nonce: card.payload.nonce, round: card.payload.round,
      instance: card.payload.instance, runtime: card.payload.runtime }, reason.trim());
  if (!isCurrent()) return;
  if (result.status !== (approve ? "approved" : "denied"))
    throw new Error("没有收到匹配的审批回执，请刷新任务核对");
}
