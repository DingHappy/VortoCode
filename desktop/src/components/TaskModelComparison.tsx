import type { ModelComparison } from "../types";

const missingLabels: Record<string, string> = {
  target_not_configured: "目标已不在配置中", incomplete_history: "历史覆盖不完整",
  no_matching_task_history: "没有同类任务记录", too_few_tasks: "同类任务不足八个",
  unreviewed_rounds: "存在未验收轮次", quality_evidence_insufficient: "质量证据不足",
  usage_not_exact: "用量缺失或含估算", pricing_missing_or_changed: "单价缺失或已变化",
  worker_cost_incomplete_or_mixed_currency: "执行费用缺失或币种不一致",
  judge_cost_incomplete_or_mixed_currency: "评判费用缺失或币种不一致", total_cost_unknown: "总费用未知",
};
function money(value: number | null, currency: string | null) {
  return value != null && Number.isFinite(value) && currency ? `${value.toFixed(6)} ${currency}` : "未知";
}

export function TaskModelComparison({ report }: { report: ModelComparison }) {
  return <details className="task-model-comparison"><summary>上次模型成本比较（参考）</summary>
    <p>{report.summary}</p>
    <small>比较时间：{report.observed_at} · 历史观察，非同题对照或实际账单；不会自动换模型。</small>
    {!report.coverage_complete && <p>历史覆盖不完整，暂不给模型排序建议。</p>}
    <div style={{ overflowX: "auto" }}><table>
      <thead><tr><th>请求模型</th><th>同类任务</th><th>每次验收费用估算</th><th>任务耗时 P95</th><th>证据</th></tr></thead>
      <tbody>{report.candidates.map(row => <tr key={`${row.provider}:${row.model}`}>
        <td>{row.provider} / {row.model}{row.current ? "（原模型）" : ""}</td>
        <td>{row.tasks} 个</td>
        <td>执行 {money(row.worker_cost_per_accepted, row.currency)}<br />评判 {money(row.judge_cost_per_accepted, row.currency)}<br />合计 {money(row.total_cost_per_accepted, row.currency)}</td>
        <td>{row.p95_elapsed_ms != null && Number.isFinite(row.p95_elapsed_ms) ? `${(row.p95_elapsed_ms / 1000).toFixed(1)} 秒` : "未知"}</td>
        <td>{row.eligible ? row.on_frontier ? "可供权衡" : "有更优历史候选" : row.missing.map(key => missingLabels[key] || "需要补充证据").join("；")}</td>
      </tr>)}</tbody>
    </table></div>
    {report.recommended_target && <p>值得进一步核对：{report.recommended_target.provider} / {report.recommended_target.model}。配置可用不代表已验证工具兼容。</p>}
  </details>;
}
