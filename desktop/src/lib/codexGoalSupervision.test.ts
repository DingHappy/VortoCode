import { expect, it } from "vitest";
import { codexGoalProgress, codexProgressFresh } from "./codexGoalSupervision";
import { goalProgressFixture } from "./codexGoalSupervision.fixtures";
const now = new Date("2026-10-31T12:00:00Z").getTime();
function fixture() {
  const { goal, snapshot } = goalProgressFixture(now);
  const input = { snapshot, goals: [goal], codexCurrent: true, goalsCurrent: true, now };
  const row = (snapshot.goal_supervision as Array<Record<string, unknown>>)[0];
  return { input, goal, snapshot, row };
}

it("joins only the same goal revision and execution and shows criterion names", () => {
  const h = fixture();
  expect(codexGoalProgress(h.input).items[0]).toMatchObject({ state: "awaiting_user", goalId: h.goal.id,
    threadId: "thread-login", canOpenGoal: true, used: 1, limit: 4, criteria: ["登录测试通过", "重启后仍可登录"] });
});
it.each(["active", "paused", "awaiting_user"])("preserves the resident's known policy state: %s", state => {
  const h = fixture(); h.row.state = state;
  expect(codexGoalProgress(h.input).items[0].state).toBe(state);
});
it.each(["revision", "goal unavailable", "codex unavailable", "old report", "future report", "old observation", "wrong thread", "wrong turn", "unknown execution", "contradictory execution", "unknown goal", "missing execution", "pending mismatch", "unknown criterion", "missing goal", "duplicate goal", "duplicate thread", "duplicate link"])(
  "cannot present a mismatched or stale association as current: %s", fault => {
    const h = fixture(), execution = h.snapshot.threads[0].execution as { ref: { session_id: string; turn_id: string }; observed_at: number; state: string };
    if (fault === "revision") h.goal.revision++;
    if (fault === "goal unavailable") h.input.goalsCurrent = false;
    if (fault === "codex unavailable") h.input.codexCurrent = false;
    if (fault === "old report") h.snapshot.received_at = new Date(now - 31 * 60_000).toISOString();
    if (fault === "future report") h.snapshot.received_at = new Date(now + 1).toISOString();
    if (fault === "old observation") execution.observed_at -= 31 * 60;
    if (fault === "wrong thread") execution.ref.session_id = "other-thread";
    if (fault === "wrong turn") execution.ref.turn_id = "other-turn";
    if (fault === "unknown execution") execution.state = "future-state";
    if (fault === "contradictory execution") execution.state = "running";
    if (fault === "unknown goal") h.goal.status = "future-status";
    if (fault === "missing execution") delete h.snapshot.threads[0].execution;
    if (fault === "pending mismatch") h.row.pending = ["criterion-1"];
    if (fault === "unknown criterion") h.row.criterion_ids = ["invented"];
    if (fault === "missing goal") h.input.goals = [];
    if (fault === "duplicate goal") h.input.goals.push(h.goal);
    if (fault === "duplicate thread") h.snapshot.threads.push(h.snapshot.threads[0]);
    if (fault === "duplicate link") (h.snapshot.goal_supervision as unknown[]).push({ ...h.row });
    const result = codexGoalProgress(h.input);
    expect(result.items.every(item => item.state === "unknown" && item.criteria.length === 0)).toBe(true);
  });
it.each([null, {}, "bad", new Array(13).fill({})])("retains a warning for an unusable additive field", value => {
  const h = fixture(); h.snapshot.goal_supervision = value;
  expect(codexGoalProgress(h.input).warnings).not.toHaveLength(0);
});
it.each([true, -1, 13, "1"])("never displays an invalid allowance as a valid count: %s", used => {
  const h = fixture(); h.row.used = used;
  expect(codexGoalProgress(h.input).items[0]).toMatchObject({ state: "unknown", used: undefined });
});
it("supports older hosts without manufacturing associations", () => {
  const h = fixture(); delete h.snapshot.goal_supervision;
  expect(codexGoalProgress(h.input)).toEqual({ items: [], warnings: [] });
});
it("accepts the backend's minimal unknown record without inventing a zero allowance", () => {
  const h = fixture(); h.snapshot.goal_supervision = [{ goal_id: h.goal.id, thread_id: "thread-login", state: "unknown", reason: "storage unavailable" }];
  expect(codexGoalProgress(h.input).items[0]).toMatchObject({ state: "unknown", used: undefined, limit: undefined, criteria: [] });
});
it("requires independent per-criterion user acceptance for a confirmed label", () => {
  const h = fixture(); h.row.state = "confirmed";
  expect(codexGoalProgress(h.input).items[0].state).toBe("unknown");
  h.goal.status = "achieved";
  h.goal.acceptance_criteria.forEach((c, i) => {
    c.status = "passed"; c.evidence_ids = [`e${i}`];
    h.goal.evidence.push({ id: `e${i}`, criterion_id: c.id, kind: "personal_acceptance", source: "user_confirmation", summary: "用户核对", passed: true });
  });
  h.row.pending = [];
  expect(codexGoalProgress(h.input).items[0].state).toBe("confirmed");
});
it("expires local reads independently of the report heartbeat", () => {
  const h = fixture(), source = { status: "current", checkedAt: now, data: h.snapshot };
  expect(codexProgressFresh(source, true, now)).toBe(true);
  expect(codexProgressFresh(source, true, now + 121_000)).toBe(false);
  expect(codexProgressFresh(source, false, now)).toBe(false);
});
