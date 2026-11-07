import type { ApprovalMetadata, WorkspaceScope } from "../types";

/** 手机连接的是常驻助理；本机通用引擎的权限不能显示成手机权限。 */
export function ownerAccessClient<T>(scope: WorkspaceScope, onHost: boolean, client: T | null): T | null {
  return scope === "general" && onHost ? client : null;
}

export interface OwnerAccessSnapshot {
  schema_version: number;
  generated_at: string;
  permissions: { profile: string; effective_level: string; scopes: string[]; taint_revokes_preauthorization: boolean };
  history: { status: string; bounded: boolean; sources?: Record<string, string>; entries: Array<ApprovalMetadata & {
    id: string; ts: string; decision: string; tainted: boolean; record_type: string; origin: string;
    domain_kind?: string; source_status?: string; decision_metadata_missing?: boolean;
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
  if (outcome === "interrupted") return "审批等待已中断";
  if (outcome === "pending") return "仍待确认";
  if (outcome === "expired") return "已过期，未放行";
  if (outcome === "cancelled") return "已取消，未放行";
  if (outcome === "approved" || (outcome == null && decision === "allowed")) return "已允许";
  if (outcome === "denied" || (outcome == null && decision === "denied")) return "已拒绝";
  return "结果待核对";
}

export type ApprovalHistoryRow = OwnerAccessSnapshot["history"]["entries"][number];

export function recordLabel(row: ApprovalHistoryRow): string {
  switch (row.record_type) {
    case "card": return "卡片处理";
    case "card_wait": return "审批等待记录";
    case "decision": return "内核决定";
    case "dispatch": return "任务单";
    case "codex_action": return row.domain_kind === "message" ? "Codex 消息" : row.domain_kind === "interrupt" ? "Codex 叫停" : "Codex 动作";
    default: return "记录类型待核对";
  }
}

export function originLabel(origin: string): string {
  return origin === "app" ? "App" : origin === "runtime" ? "助理运行端" : origin === "im" ? "IM" : "入口待核对";
}

export function domainStatus(row: ApprovalHistoryRow): string | undefined {
  if (row.source_status === undefined) return undefined;
  if (row.record_type === "card_wait") return row.source_status === "pending" ? "当前卡片仍在等你确认，请到对话中处理"
    : row.source_status === "interrupted" ? "当前服务已无法接续旧卡，请先核对主聊天；不会重放批准或恢复执行"
    : "原审批等待状态无法核对，请先检查主聊天";
  const labels = row.record_type === "codex_action" ? new Map([
    ["proposed", "待批准"], ["approved", "已批准，等待交给 Codex"], ["sending", "正在交给 Codex，回执待核对"],
    ["sent", "Codex 已受理，执行结果仍需核对"], ["failed", "来源记录为未发送成功"],
    ["cancelled", "动作已撤回"], ["expired", "动作已过时"], ["unknown", "发送结果无法核对"],
  ]) : new Map([
    ["proposed", "待批准"], ["sending", "正在下发，结果待核对"], ["send_failed", "下发前失败，未提交"],
    ["submission_unknown", "下发结果无法核对"], ["running", "运行端报告进行中"], ["waiting", "运行端等待处理"],
    ["done", "运行端报告结束，验收仍需核对"], ["failed", "运行端报告失败"], ["cancelled", "任务单已取消"],
    ["stopped", "运行端已暂停或中断"],
  ]);
  return labels.get(row.source_status) ?? "来源状态待核对";
}

export function unavailableHistorySources(value: OwnerAccessSnapshot): string[] {
  const labels = new Map([["audit", "审批审计"], ["app_cards", "回合审批"], ["codex", "Codex 动作"], ["dispatches", "任务单"]]);
  return Object.entries(value.history.sources ?? {}).filter(([, status]) => status !== "current").map(([name]) => labels.get(name) ?? "未知来源");
}
