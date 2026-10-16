/** Synthetic UI fixture: no runtime, model, native IPC, credentials or persistence. */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { TaskCard } from "../src/components/TaskCard";
import { taskCollaborationDraft } from "../src/lib/taskCollaboration";
import type { ModelComparison, TaskItem } from "../src/types";
import "../src/App.css";

const report: ModelComparison = { round: 1, observed_at: "2030-01-01T00:00:00Z", status: "candidate_for_review",
  summary: "发现历史费用更低且质量与耗时不劣的已配置候选，需先核对再选择", coverage_complete: true,
  recommended_target: { provider: "sample", model: "reader-small" }, candidates: [
    { provider: "sample", model: "reader-large", configured: true, current: true, eligible: true, on_frontier: false,
      tasks: 8, accepted: 8, acceptance_lower_bound: .67, p95_elapsed_ms: 3000, currency: "CNY", missing: [],
      worker_cost_per_accepted: .02, judge_cost_per_accepted: .01, total_cost_per_accepted: .03, one_more_worker_round_estimate: .02 },
    { provider: "sample", model: "reader-small", configured: true, current: false, eligible: true, on_frontier: true,
      tasks: 8, accepted: 8, acceptance_lower_bound: .67, p95_elapsed_ms: 1500, currency: "CNY", missing: [],
      worker_cost_per_accepted: .01, judge_cost_per_accepted: 0, total_cost_per_accepted: .01, one_more_worker_round_estimate: .01 },
  ] };

function Fixture() {
  const [sparse, setSparse] = useState(false), [round, setRound] = useState(1), [draft, setDraft] = useState("");
  const comparison: ModelComparison = sparse ? { ...report, coverage_complete: false, recommended_target: null,
    summary: "原模型或候选缺少可比证据，先补样本、用量或价格", candidates: report.candidates.map(row => ({ ...row,
      eligible: false, on_frontier: false, total_cost_per_accepted: null, judge_cost_per_accepted: null,
      missing: ["incomplete_history", "judge_cost_incomplete_or_mixed_currency", "pricing_missing_or_changed"] })) } : report;
  const task: TaskItem = { id: "task-sample", kind: "delegation", status: "done", owner_session: "sid-sample",
    prompt: "分析示例模块（虚构数据）",
    result: "示例交付待核对", collaboration: { round, assignee: "reader", acceptance: ["说明示例入口"],
      review: "pending", tainted: false, messages: [], model_comparison: comparison } };
  return <main style={{ maxWidth: 1000, padding: 24, margin: "0 auto" }}>
    <p>隔离界面验收：虚构模型、价格与任务；不调用模型或保存配置。</p>
    <button onClick={() => setSparse(!sparse)}>切换证据充分 / 不足</button>
    <button onClick={() => setRound(round === 1 ? 2 : 1)}>切换任务轮次</button>
    <TaskCard task={task} focused={false} taskReviewVerifying={false} getClient={() => null} onDispatchUpdated={() => {}}
      loadPlanGraph={async () => { throw new Error("fixture has no plan"); }} onPause={() => {}} onResume={() => {}}
      onCancel={() => {}} onCopy={() => {}} onReviewBranch={() => {}} onVerify={() => {}} onOpenPr={() => {}}
      onCollaboration={(value, action) => setDraft(taskCollaborationDraft(value, action)?.text || "")} />
    <label>仅准备的请求草稿<textarea aria-label="请求草稿" value={draft} readOnly rows={6} style={{ width: "100%" }} /></label>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
