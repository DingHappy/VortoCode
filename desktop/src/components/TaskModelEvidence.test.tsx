import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TaskCard } from "./TaskCard";
import type { TaskItem } from "../types";

function markup(task: TaskItem, teamContext?: Parameters<typeof TaskCard>[0]["teamContext"]) {
  return renderToStaticMarkup(<TaskCard task={task} focused={false} taskReviewVerifying={false}
    loadPlanGraph={async () => { throw new Error("unused"); }} onPause={() => {}} onResume={() => {}}
    onCancel={() => {}} onCopy={() => {}} onReviewBranch={() => {}} onVerify={() => {}}
    onOpenPr={() => {}} onCollaboration={() => {}} getClient={() => null} onDispatchUpdated={() => {}} teamContext={teamContext} />);
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

it("puts a focused readable result before raw messages and keeps long original requirements collapsed", () => {
  const html = markup({ ...task, prompt: "整理日报。" + "完整背景与底稿。".repeat(100), result: "## 日报正文\n\n已交回，待验收。" });
  expect(html).toContain('class="task-request"');
  expect(html).toContain("查看完整要求"); expect(html).toContain("日报正文");
  expect(html.indexOf("交给主 Agent 验收")).toBeLessThan(html.indexOf("查看交付结果"));
});

it("routes a known internal review to its coordinator while retaining independent owner review actions", () => {
  const internal = markup(task, { id: "team", objective: "日报", member: "研究员" });
  expect(internal).toContain("等待协调人审阅");
  expect(internal).not.toContain("交给主 Agent 验收");
  expect(internal).not.toContain("补充要求 / 返工");
  expect(markup(task)).toContain("交给主 Agent 验收");
});
