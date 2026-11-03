import type { ApprovalMetadata } from "../types";

export interface OwnerAccessSnapshot {
  schema_version: number;
  generated_at: string;
  permissions: { profile: string; effective_level: string; scopes: string[]; taint_revokes_preauthorization: boolean };
  history: { status: string; bounded: boolean; entries: Array<ApprovalMetadata & {
    id: string; ts: string; decision: string; tainted: boolean; record_type: string; origin: string;
  }> };
}
export function ownerAccessSnapshot(raw: OwnerAccessSnapshot): OwnerAccessSnapshot {
  if (raw?.schema_version !== 1 || typeof raw.generated_at !== "string" || raw.permissions?.profile !== "owner_device"
    || !["ask", "reads"].includes(raw.permissions.effective_level) || raw.permissions.taint_revokes_preauthorization !== true
    || !Array.isArray(raw.permissions.scopes) || raw.permissions.scopes.length !== 1 || raw.permissions.scopes[0] !== "once"
    || raw.history?.bounded !== true || !Array.isArray(raw.history.entries) || raw.history.entries.length > 100
    || raw.history.entries.some(row => !row || typeof row.id !== "string" || typeof row.ts !== "string"
      || typeof row.decision !== "string" || typeof row.tainted !== "boolean")
    || new Set(raw.history.entries.map(row => row.id)).size !== raw.history.entries.length) throw new Error("权限与审批记录合同待核对");
  return raw;
}
export function approvalOutcome(outcome: unknown, decision: string): string {
  if (outcome === "expired") return "已过期，未放行";
  if (outcome === "cancelled") return "已取消，未放行";
  if (outcome === "approved" || (outcome == null && decision === "allowed")) return "已允许";
  if (outcome === "denied" || (outcome == null && decision === "denied")) return "已拒绝";
  return "结果待核对";
}
