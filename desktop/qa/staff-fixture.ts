// Offline settings fixture: no live API, Keychain or task execution.
import type { StaffClient, StaffSnapshot } from "../src/lib/staff";
const calendar = { id: "calendar-reader", title: "只读日程", brief: "查看日程，列出冲突和需要主人决定的事。", enabled: true, builtin: false, revision: 1,
  grants: { operations: ["read"], tools: ["calendar_events"], sees: ["plugin:calendar"], web: false, project: false }, engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 };
const value: StaffSnapshot = { roles: [calendar], members: [{ id: "staff-calendar", name: "日程专员", role: calendar.id, engine: "vortocode", model: "", status: "active", revision: 1, busy: 0,
  recent_tasks: [{ id: "task-fixture", prompt: "检查明天的安排，列出需要调整的冲突", status: "done", review: "pending", delivery: "review" }] }],
  proposals: [{ id: "staff-proposal-" + "a".repeat(32), digest: "b".repeat(64), nonce: "c".repeat(32), status: "pending", requires_desktop_key: true,
    reason: "为日历查询登记独立的只读岗位，不给联网和修改权限。", snapshot: { kind: "role", data: { ...calendar, title: "日历核对" }, previous: null } }],
  approval_key: { instance: "d".repeat(32), key: null } };
export const staffFixtureClient: StaffClient = {
  async readStaff() { return structuredClone(value); },
  async decideStaff() { throw new Error("夹具不执行真实批准"); },
  async bindStaffKey() { throw new Error("夹具不登记真实钥匙"); },
  async setStaffStatus(id, status, revision) {
    const member = value.members.find(item => item.id === id);
    if (!member || member.revision !== revision) throw new Error("夹具版本已变化");
    member.status = status; member.revision++; return {};
  },
};
