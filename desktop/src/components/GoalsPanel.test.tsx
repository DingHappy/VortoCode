import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { GoalsPanel } from "./GoalsPanel";
import type { GoalItem } from "../types";
const goal: GoalItem = {id: "target", objective: "指定目标", status: "draft", acceptance_criteria: [], constraints: [], non_goals: [], task_ids: [], evidence: [], progress: {passed: 0, failed: 0, total: 0}};
const noop = () => {};
const props = {goals: [goal, {...goal, id: "other", objective: "其它目标"}], tasks: [], runs: [], connection: "connected" as const, editingGoalId: null, goalObjective: "", goalCriteria: "", goalConstraints: "", goalNonGoals: "", goalSubmitting: false, goalEvidenceDrafts: {}, goalVerifierDrafts: {}, onObjectiveChange: noop, onCriteriaChange: noop, onConstraintsChange: noop, onNonGoalsChange: noop, onEvidenceDraftsChange: noop, onVerifierDraftsChange: noop, onResetForm: noop, onSaveDraft: noop, onEditDraft: noop, onDeleteDraft: noop, onRunGoal: noop, onRunVerifiers: noop, onSaveVerifier: noop, onRecordEvidence: noop};
it("unverified navigation hides all remembered target cards and execution actions", () => {
  const html = renderToStaticMarkup(<GoalsPanel {...props} goalFocus={{id: "target", status: "error", error: "404"}} />);
  expect(html).toContain("目标未核验"); expect(html).not.toContain('data-goal-id');
  expect(html).not.toContain("确认并开始执行"); expect(html).toContain("重新读取指定目标");
});
it("a target outside the bounded list renders exactly that detail", () => {
  const html = renderToStaticMarkup(<GoalsPanel {...props} goals={[]} goalFocus={{id: "target", status: "ready", goal}} />);
  expect(html).toContain('data-goal-id="target"'); expect(html).not.toContain("还没有开发目标");
});
it("focused goals display later authoritative mutations from the original list", () => {
  const html = renderToStaticMarkup(<GoalsPanel {...props} goals={[{...goal, objective: "更新后的合同"}]} goalFocus={{id: "target", status: "ready", goal}} />);
  expect(html).toContain("更新后的合同"); expect(html).not.toContain("<h3>指定目标</h3>"); expect(html).not.toContain("其它目标");
});
