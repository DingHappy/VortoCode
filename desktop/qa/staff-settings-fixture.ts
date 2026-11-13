// Deterministic fixture for the production settings/controller, with no Gateway or native I/O.
import type { StaffClient, StaffConfirmation, StaffMember, StaffRole, StaffSnapshot } from "../src/lib/staff";
import type { RoutinesClient } from "../src/lib/routines";

type Receipt = { action: string; id?: string; status?: string; revision?: number; approve?: boolean; confirmation?: StaffConfirmation };
export function staffSettingsFixture(mode: string) {
  const privateRole: StaffRole = { id: "qa-calendar-read", title: "日程只读岗位", brief: "整理今天和明天的安排，提醒需要留出时间的事项。",
    enabled: true, revision: 1, builtin: false, grants: { operations: ["read"], tools: ["calendar_events"], sees: ["plugin:calendar"], web: false, project: false },
    engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 };
  const publicRole: StaffRole = { id: "qa-public-research", title: "公开资料岗位", brief: "跟踪公开资料，整理摘要和原始出处。",
    enabled: true, revision: 1, builtin: false, grants: { operations: ["read"], tools: ["web_search", "web_fetch"], sees: [], web: true, project: false },
    engine: ["vortocode"], max_steps: 16, timeout_seconds: 900 };
  const members: StaffMember[] = [
    { id: "qa-calendar", name: "日程专员", role: privateRole.id, engine: "vortocode", model: "", revision: 1, status: "active", busy: 1,
      recent_tasks: [{ id: "qa-task-calendar", prompt: "整理明天的安排", status: "done", review: "pending", delivery: "review" }] },
    { id: "qa-research", name: "资料专员", role: publicRole.id, engine: "vortocode", model: "", revision: 1, status: "paused", busy: 0, recent_tasks: [] },
  ];
  if (mode === "retired") members[1].status = "retired";
  if (mode === "unknown-role") members[0].role = "qa-missing-role";
  if (mode === "paused") members[0] = { ...members[0], status: "paused", busy: 0 };
  const snapshot: StaffSnapshot = { roles: [privateRole, publicRole], members: mode === "empty" ? [] : members,
    proposals: mode === "empty" ? [] : [{ id: "qa-proposal", status: "pending", reason: "只读核对日程，不修改或发送。", approval_level: "normal",
      digest: "qa-digest-1", nonce: "qa-nonce-1", round: 1, instance: "qa-instance", snapshot: { kind: "role", data: { ...privateRole }, previous: null } }],
    budget_settings: { revision: 1, daily_tokens: 20000, task_tokens: 5000 }, usage: { day: "2026-11-13", total: 1048 },
    standing_specialists: [{ id: "calendar", title: "日程专员", scope: "只读日历，不联网", guide: "说明每天的汇报时间和时区。" },
      { id: "mail", title: "邮件专员", scope: "只读邮件；保存草稿须逐次确认，不发送。", guide: "说明需要关注的邮件范围。" },
      { id: "research", title: "资料专员", scope: "只查公开资料，不读取私人来源。", guide: "说明要跟踪的公开话题。" }],
    development_projects: [{ name: "合成项目" }],
  };
  let reads = 0;
  const receipts: Receipt[] = [];
  const client: StaffClient & RoutinesClient = {
    async readStaff() {
      reads++;
      if ((mode === "first-error" && reads === 1) || (mode === "stale-error" && reads === 2)) throw new Error("合成名录暂时无法读取");
      return structuredClone(snapshot);
    },
    async setStaffStatus(id, status, revision) {
      const member = snapshot.members.find(item => item.id === id);
      if (!member || member.revision !== revision) throw new Error("合成专员记录已变化");
      receipts.push({ action: "status", id, status, revision });
      member.status = status; member.revision++;
      return {};
    },
    async decideStaff(id, approve, confirmation) {
      const proposal = snapshot.proposals.find(item => item.id === id);
      if (!proposal || proposal.status !== "pending" || ["digest", "nonce", "round", "instance"].some(key => proposal[key as keyof StaffConfirmation] !== confirmation[key as keyof StaffConfirmation]))
        throw new Error("合成提议身份不符");
      receipts.push({ action: "decide", id, approve, confirmation: { ...confirmation } });
      proposal.status = approve ? "approved" : "denied";
      return {};
    },
    async setStaffBudgets(settings) {
      if (snapshot.budget_settings?.revision !== settings.revision) throw new Error("合成额度记录已变化");
      receipts.push({ action: "budget", revision: settings.revision });
      snapshot.budget_settings = { ...settings, revision: settings.revision + 1 };
      return structuredClone(snapshot.budget_settings);
    },
    async proposeDevelopment(data) { receipts.push({ action: "development", id: data.id }); return {}; },
    async readStaffRecovery() { return { instance: "qa-instance", items: [] }; },
    async decideStaffRecovery() { throw new Error("合成夹具没有可恢复任务"); },
    async readRoutines() { return { revision: 1, timezone: "Asia/Shanghai", host_timezone: "UTC", items: [], scheduler_enabled: false, migrations: [], migration: null }; },
    async changeRoutines() { throw new Error("本夹具只验证专员设置，不修改例行工作"); },
    async readApprovalStats(days) { return { days, timezone: "UTC", from: "2026-11-07T00:00:00Z", to: "2026-11-14T00:00:00Z", groups: [],
      totals: { count: 0, timed: 0, approved: 0, denied: 0, expired: 0, pending: 0, cancelled: 0, interrupted: 0, unknown: 0, median_wait_seconds: null },
      automatic: {}, system_denied: 0, coverage: { audit: "current", tasks: "current", missing_wait: 0, unknown_metadata: 0, invalid_timestamps: 0 } }; },
  };
  return { client, read: () => ({ reads, receipts: structuredClone(receipts) }) };
}
