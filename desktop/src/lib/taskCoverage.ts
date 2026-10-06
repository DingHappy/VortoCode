import type { TaskCoverageReport } from "../types";

/** Validate server coverage before committing a cursor or displaying success. */
export function coverageMatches(report: TaskCoverageReport, taskId: string, round: number, requestId?: string,
  mode: TaskCoverageReport["mode"] = "reconcile") {
  const c = report?.coverage;
  if (!c || report.source_task_id !== taskId || report.source_round !== round || report.mode !== mode
      || (requestId && report.request_id !== requestId) || !/^scan-[a-f0-9]{24}$/.test(report.scan_id)
      || !["records", "process", "finished"].includes(report.phase)
      || !["complete", "incomplete", "unknown"].includes(c.state)
      || report.order !== "filename_utf8_bytes_ascending" || report.validity !== "observed_namespace_and_lstat"
      || !Number.isFinite(Date.parse(report.observed_at)) || typeof report.complete !== "boolean"
      || typeof report.snapshot_valid !== "boolean" || typeof c.graph_complete !== "boolean"
      || !Number.isInteger(report.page_size) || report.page_size < 1 || report.page_size > 512
      || !Number.isInteger(report.sequence) || report.sequence < 0
      || !/^[a-f0-9]{64}$/.test(report.snapshot_id) || !/^[a-f0-9]{64}$/.test(report.evidence_digest)
      || report.receipt_count !== report.sequence
      || !Array.isArray(c.reasons) || !c.reasons.every(item => typeof item === "string")) return false;
  const counts = [c.entries_total, c.entries_read, c.record_bytes, c.skipped, c.damaged,
    c.edge_visits, c.selected, c.processed, c.reconciled, c.failures];
  if (counts.some(value => !Number.isInteger(value) || value < 0) || c.entries_total > 8192
      || c.entries_read > c.entries_total || c.selected > 1024 || c.processed > c.selected
      || c.reconciled !== (mode === "reconcile" ? c.processed : 0) || c.edge_visits > 8192 || c.record_bytes > 67108864) return false;
  if ((c.state === "complete") !== report.complete || (!report.snapshot_valid && c.state !== "unknown")) return false;
  if (report.next_cursor !== null && (typeof report.next_cursor !== "string"
      || !report.next_cursor.startsWith(`${report.scan_id}.${report.sequence}.`)
      || report.phase === "finished" || !report.snapshot_valid)) return false;
  return !report.complete || (c.state === "complete" && report.snapshot_valid && report.phase === "finished"
    && report.next_cursor === null && c.entries_read === c.entries_total && c.graph_complete
    && c.processed === c.selected && !c.skipped && !c.damaged && !c.failures && !c.reasons.length);
}

const reasons: Record<string, string> = {
  snapshot_changed: "分页期间记录已变化，请重新开始核对。",
  selected_identity_changed: "下游身份或内容已变化，请重新开始核对。",
  source_identity_changed: "前置身份或轮次已变化，请刷新任务后重新核对。",
  inventory_budget: "台账超出本次快照范围，覆盖未知。",
  inventory_unreadable: "无法读取台账，覆盖未知。",
  records_unreadable: "存在无法读取的记录，不能确认完整覆盖。",
  contracts_damaged: "存在损坏的任务合同，不能确认完整覆盖。",
  reconciliation_failed: "部分任务核对失败，覆盖未知。",
  page_budget: "本轮页数达到上限，未完整覆盖；可增加每页范围后重新开始。",
  total_read_budget: "本轮读取达到上限，未完成全部核对。",
  graph_depth_budget: "依赖深度超出本轮范围，未完整覆盖。",
  graph_node_budget: "下游数量超出本轮范围，未完整覆盖。",
  graph_edge_budget: "依赖关系超出本轮范围，未完整覆盖。",
};

export function coverageLabel(report: TaskCoverageReport) {
  return report.complete ? "上次覆盖核对已完成" : report.coverage.state === "unknown" ? "覆盖状态未知" : "覆盖核对未完成";
}
export function coverageReasons(report: TaskCoverageReport) {
  return report.coverage.reasons.map(reason => reasons[reason] || "存在未确认的覆盖范围，请检查任务状态。");
}
