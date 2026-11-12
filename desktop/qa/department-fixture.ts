import type { DepartmentDetail } from "../src/lib/departments";
import type { StaffSnapshot } from "../src/lib/staff";

export function departmentFixture(): DepartmentDetail {
  const role = { id: "public-reader", title: "公开资料", brief: "查公开资料，交回出处", builtin: false, enabled: true, revision: 1,
    grants: { operations: ["read"], tools: ["web_search"], sees: [], web: true, project: false }, engine: ["vortocode"], max_steps: 8, timeout_seconds: 600 };
  const member = { id: "staff-reader", name: "资料专员", role: role.id, engine: "vortocode", model: "", revision: 1,
    status: "active" as const, busy: 1, department: "dept-public", recent_tasks: [] };
  return { department: { id: "dept-public", name: "资料整理", duty: "比较公开资料，交回有出处的结论", members: [member.id], lead: "",
    revision: 1, status: "active", limits: { parallel: 2, daily_tokens: 1000 }, notes_revision: 1 },
    members: [member], roles: [role], tasks_complete: true, tasks: [{ id: "task-0123456789", kind: "delegation", status: "done",
      prompt: "比较两份公开材料，写清仍待核实的地方", result: "## 交付结论（合成）\n\n已列出差异；日期仍需核对。此处为测试资料。",
      collaboration: { assignee: member.id, round: 1, review: "pending", acceptance: ["附出处"], messages: [], tainted: true,
        dispatch: { source: "staff", department: "dept-public", max_steps: 8, timeout_seconds: 600 } } }],
    notes: { current: { revision: 1, text: "- 每份材料注明日期和出处\n", sources: [], tainted: false },
      snapshot: { revision: 1, text: "- 每份材料注明日期和出处\n", sources: [], tainted: false, digest: "s".repeat(64) },
      pending: [{ id: "task-0123456789:1:0", digest: "a".repeat(64), text: "把尚未核实的事实单列出来", task_id: "task-0123456789", round: 1, member: member.id, sources: [], tainted: true },
        { id: "task-0123456789:1:1", digest: "b".repeat(64), text: "<script>这个条目只能按原文显示</script>", task_id: "task-0123456789", round: 1, member: member.id, sources: [], tainted: true }],
      history: [], error: "", applying: null }, usage: { day: "2026-11-10", total: 300, departments: { "dept-public": 300 }, members: { [member.id]: 300 } },
    budget_settings: { revision: 1, daily_tokens: 0, task_tokens: 0 } };
}
export function directoryFixture(detail = departmentFixture()): StaffSnapshot {
  return { roles: detail.roles, members: detail.members, departments: [{ ...detail.department, pending_notes: detail.notes.pending.length }],
    proposals: [], usage: detail.usage, budget_settings: detail.budget_settings };
}
