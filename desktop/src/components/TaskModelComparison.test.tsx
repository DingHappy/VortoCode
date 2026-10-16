import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TaskModelComparison } from "./TaskModelComparison";
import type { ModelComparison } from "../types";

const report: ModelComparison = { round: 1, observed_at: "2030-01-01T00:00:00Z", status: "candidate_for_review",
  summary: "发现值得核对的候选", recommended_target: { provider: "worker", model: "small" }, coverage_complete: true,
  candidates: [{ provider: "worker", model: "small", configured: true, current: false, eligible: true, on_frontier: true,
    tasks: 8, accepted: 8, acceptance_lower_bound: .67, p95_elapsed_ms: 1500, currency: "CNY", missing: [],
    worker_cost_per_accepted: .01, judge_cost_per_accepted: .02, total_cost_per_accepted: .03, one_more_worker_round_estimate: .01 }] };

it("separates worker and judge fees and labels observational evidence", () => {
  const html = renderToStaticMarkup(<TaskModelComparison report={report} />);
  for (const text of ["上次模型成本比较", "8", "0.010000 CNY", "0.020000 CNY", "0.030000 CNY", "1.5 秒", "非同题对照", "不会自动换模型", "不代表已验证工具兼容"]) expect(html).toContain(text);
});
it("shows unknown costs and translated evidence gaps without ranking", () => {
  const next: ModelComparison = { ...report, coverage_complete: false, recommended_target: null,
    candidates: [{ ...report.candidates[0], eligible: false, on_frontier: false, total_cost_per_accepted: null,
      judge_cost_per_accepted: null, missing: ["judge_cost_incomplete_or_mixed_currency", "pricing_missing_or_changed"] }] };
  const html = renderToStaticMarkup(<TaskModelComparison report={next} />);
  for (const text of ["未知", "评判费用缺失", "单价缺失或已变化", "暂不给模型排序建议"]) expect(html).toContain(text);
  expect(html).not.toContain("judge_cost_incomplete"); expect(html).not.toContain("值得进一步核对");
});
it("keeps explicit zero estimates and handles non-finite transport values", () => {
  const next: ModelComparison = { ...report, candidates: [{ ...report.candidates[0], worker_cost_per_accepted: 0,
    judge_cost_per_accepted: 0, total_cost_per_accepted: Number.NaN, p95_elapsed_ms: Number.POSITIVE_INFINITY }] };
  const html = renderToStaticMarkup(<TaskModelComparison report={next} />);
  expect(html).toContain("0.000000 CNY"); expect(html).toContain("未知");
  expect(html).not.toContain("NaN"); expect(html).not.toContain("Infinity");
});
