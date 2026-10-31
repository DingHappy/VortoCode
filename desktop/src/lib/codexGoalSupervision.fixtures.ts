/** Synthetic read-only preview/test data; never connected to a model or service. */
import type { CodexProgressSnapshot, PersonalGoal } from "../types";

export function goalProgressFixture(now = Date.now()): { goal: PersonalGoal; snapshot: CodexProgressSnapshot } {
  const stamp = new Date(now).toISOString();
  return {
    goal: { id: "personal-login", kind: "personal", revision: 3, objective: "修复登录并确认重启后可用", status: "active",
      constraints: [], non_goals: [], task_ids: [], next_action: "核对登录与重启结果", blocker: "", created: stamp, updated: stamp,
      due_at: "", follow_up_at: "", overdue: false, follow_up_due: false, progress: { total: 2, passed: 0, failed: 0 },
      acceptance_criteria: [{ id: "criterion-1", text: "登录测试通过", status: "pending", evidence_ids: [] },
        { id: "criterion-2", text: "重启后仍可登录", status: "pending", evidence_ids: [] }], evidence: [] },
    snapshot: { overview: "", received_at: stamp, threads: [{ id: "thread-login", name: "修复登录", cwd: "/projects/login",
      status: "idle", flags: [], updated_at: now / 1000, turn: { id: "turn-1", status: "completed", request: "修复登录", reply: "本轮修改已结束" },
      execution: { schema_version: 1, ref: { backend_id: "codex", instance_id: "mac-1", session_id: "thread-login",
        run_id: "turn-1", turn_id: "turn-1", task_id: "", scope: "runtime" }, state: "finished", observed_at: now / 1000 } }],
      goal_supervision: [{ goal_id: "personal-login", thread_id: "thread-login", revision: 3,
        state: "awaiting_user", reason: "本轮已结束，目标仍缺逐项用户验收", used: 1, limit: 4,
        criterion_ids: ["criterion-1", "criterion-2"], pending: ["criterion-1", "criterion-2"] }] },
  };
}
