import { expect, it } from "vitest";
import type { PersonalGoal } from "../types";
import { hasConfirmedPersonalCriterion, hasConfirmedPersonalGoal, readPersonalGoalSnapshot } from "./personalGoalSnapshot";

const goal: PersonalGoal = { id: "personal-release", kind: "personal", revision: 2, objective: "核对发布", status: "achieved",
  constraints: [], non_goals: [], task_ids: [], due_at: "", follow_up_at: "", overdue: false, follow_up_due: false,
  progress: { passed: 1, failed: 0, total: 1 },
  acceptance_criteria: [{ id: "c1", text: "核对交付依据", status: "passed", evidence_ids: ["e1"] }],
  evidence: [{ id: "e1", criterion_id: "c1", kind: "personal_acceptance", source: "user_confirmation", summary: "逐项核对", passed: true }] };

it("retains valid snapshots and additive metadata without rewriting records", () => {
  const value = [{ ...goal, extension: { backend: "future" }, due_at: "2026-10-24T10:30+08:00" }];
  expect(readPersonalGoalSnapshot(value)).toBe(value);
  expect(hasConfirmedPersonalGoal(value[0])).toBe(true);
});

it.each([
  ["missing progress", { ...goal, progress: undefined }],
  ["impossible counts", { ...goal, progress: { passed: 2, failed: 0, total: 1 } }],
  ["fractional counts", { ...goal, progress: { passed: 0.5, failed: 0, total: 1 } }],
  ["invalid date", { ...goal, due_at: "not-a-date" }],
  ["normalized invalid calendar", { ...goal, due_at: "2026-02-30T10:00:00Z" }],
  ["date outside the supported calendar", { ...goal, due_at: "+275760-09-13T00:00:00Z" }],
  ["date without zone", { ...goal, follow_up_at: "2026-10-24T12:00:00" }],
  ["criterion object text", { ...goal, acceptance_criteria: [{ ...goal.acceptance_criteria[0], text: {} }] }],
  ["duplicate criteria", { ...goal, acceptance_criteria: [goal.acceptance_criteria[0], goal.acceptance_criteria[0]], progress: { passed: 2, failed: 0, total: 2 } }],
  ["duplicate evidence", { ...goal, evidence: [goal.evidence[0], goal.evidence[0]] }],
  ["evidence object text", { ...goal, evidence: [{ ...goal.evidence[0], summary: {} }] }],
  ["truthy acceptance", { ...goal, evidence: [{ ...goal.evidence[0], passed: "true" }] }],
  ["invalid reminder history", { ...goal, follow_up_history: [null] }],
  ["invalid reminder time", { ...goal, follow_up: { accepted_at: "broken" } }],
  ["wrong kind", { ...goal, kind: "project" }],
  ["unsafe revision", { ...goal, revision: Number.MAX_SAFE_INTEGER + 1 }],
  ["future scope revision", { ...goal, scope_revision: 3 }],
  ["boolean scope revision", { ...goal, scope_revision: true }],
  ["negative scope revision", { ...goal, scope_revision: -1 }],
])("rejects unsafe snapshot fields: %s", (_label, value) => {
  expect(() => readPersonalGoalSnapshot([value])).toThrow("个人目标数据格式异常");
});

it.each([null, {}, [goal, goal]])("rejects unusable lists without treating them as empty", value => {
  expect(() => readPersonalGoalSnapshot(value)).toThrow();
});

it("allows empty results and structurally valid but incomplete evidence without certifying completion", () => {
  expect(readPersonalGoalSnapshot([])).toEqual([]);
  const [unproven] = readPersonalGoalSnapshot([{ ...goal, evidence: [] }]);
  expect(hasConfirmedPersonalGoal(unproven)).toBe(false);
  expect(hasConfirmedPersonalCriterion(unproven, unproven.acceptance_criteria[0])).toBe(false);
});

it("keeps unknown state names readable and never promotes them to acceptance", () => {
  const [future] = readPersonalGoalSnapshot([{ ...goal, status: "future", follow_up: { status: "future-reminder" } }]);
  expect(future.status).toBe("future");
  expect(hasConfirmedPersonalGoal(future)).toBe(false);
  const [partial] = readPersonalGoalSnapshot([{ ...goal, status: "active" }]);
  expect(hasConfirmedPersonalCriterion(partial, partial.acceptance_criteria[0])).toBe(true);
  expect(hasConfirmedPersonalGoal(partial)).toBe(false);
});
