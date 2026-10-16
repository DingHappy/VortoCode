import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TaskCard } from "./TaskCard";
import type { TaskItem } from "../types";

function markup(task: TaskItem) {
  return renderToStaticMarkup(<TaskCard task={task} focused={false} taskReviewVerifying={false}
    loadPlanGraph={async () => { throw new Error("unused"); }} onPause={() => {}} onResume={() => {}}
    onCancel={() => {}} onCopy={() => {}} onReviewBranch={() => {}} onVerify={() => {}}
    onOpenPr={() => {}} onCollaboration={() => {}} getClient={() => null} onDispatchUpdated={() => {}} />);
}
const task: TaskItem = { id: "task-example", kind: "delegation", status: "done", owner_session: "sid-example",
  collaboration: { assignee: "reader", round: 1, review: "pending", acceptance: [], tainted: false, messages: [],
    model_observations: [{ round: 1, tokens: 120, elapsed_ms: 1500, usage_evidence: "accounted_responses",
      cost_estimate: null, currency: null, components: [{ estimated_calls: 1 }] }],
    model_assessment: { round: 1, state: "complete", action: "evidence_needed", reason: "先补验证证据", source: "fast_rules" } } };

it("shows usage estimates and unknown prices without granting acceptance", () => {
  const html = markup(task);
  for (const label of ["120 tokens", "含估算", "1.5", "费用估算：未知", "非账单", "先补验证证据", "仅建议，不自动执行或验收", "待验收"]) expect(html).toContain(label);
  expect(html).toContain("评估模型与成本");
});
it("does not display an old round's model advice or usage as current", () => {
  const html = markup({ ...task, collaboration: { ...task.collaboration!, round: 2 } });
  expect(html).not.toContain("120 tokens"); expect(html).not.toContain("先补验证证据");
});
