import { invoke } from "@tauri-apps/api/core";
import type { StaffProof } from "./staff";

export interface StaffAction {
  digest: string;
  status: "pending" | "approved" | "denied" | "cancelled" | "expired" | "interrupted";
  reason: string;
  decision: boolean | null;
  payload: {
    id: string; task_id: string; round: number; member: string; member_revision: number;
    member_name?: string; role_title?: string;
    role: string; role_revision: number; instance: string; key_id: string; nonce: string;
    tool: string; args: Record<string, unknown>; kind: string; message: string;
    tainted: boolean; required_key: "device" | "approval"; expires_at: number;
  };
}
export interface StaffActionClient {
  readStaffAction(task: string): Promise<StaffAction>;
  decideStaffAction(task: string, action: string, approve: boolean, digest: string,
    reason: string, signature?: string): Promise<{ status: string; replayed: boolean }>;
}
export function signStaffAction(card: StaffAction): Promise<StaffProof> {
  const p = card.payload;
  return invoke<StaffProof>("staff_action_sign", { instance: p.instance, keyId: p.key_id,
    taskId: p.task_id, round: p.round, actionId: p.id, digest: card.digest, nonce: p.nonce })
    .catch(error => { throw new Error(typeof error === "string" ? error : "批准钥匙未完成，动作仍在等待"); });
}

/** A late signature, changed round, changed card or disconnected client cannot send a decision. */
export async function decideStaffAction(client: StaffActionClient, expected: StaffAction, approve: boolean,
  reason: string, isCurrent: () => boolean, sign = signStaffAction) {
  if (!approve && !reason.trim()) throw new Error("请填写拒绝理由，专员需要据此调整做法");
  const card = await client.readStaffAction(expected.payload.task_id);
  if (!isCurrent()) return;
  if (card.status !== "pending" || card.digest !== expected.digest || card.payload.id !== expected.payload.id
    || card.payload.task_id !== expected.payload.task_id || card.payload.round !== expected.payload.round)
    throw new Error("审批卡已变化或失效，请刷新后核对");
  let signature: string | undefined;
  if (approve && card.payload.required_key === "approval") {
    if (!card.payload.key_id) throw new Error("请先在设置 → 团队与权限登记批准钥匙，再重新核对任务");
    const proof = await sign(card);
    if (!isCurrent()) return;
    if (!proof.signature) throw new Error("未收到批准钥匙签名，动作仍在等待");
    signature = proof.signature;
  }
  if (!isCurrent()) return;
  const result = await client.decideStaffAction(card.payload.task_id, card.payload.id, approve,
    card.digest, reason.trim(), signature);
  if (!isCurrent()) return;
  if (result.status !== (approve ? "approved" : "denied"))
    throw new Error("没有收到匹配的审批回执，请刷新任务核对");
}
