import type { TaskCoverageObservation, TaskCoverageObservationRef, TaskCoverageRecords } from "../types";
import { coverageMatches } from "./taskCoverage";

export function observationRef(row: TaskCoverageRecords["active"][number]): TaskCoverageObservationRef {
  return { scan_id: row.report.scan_id, round: row.report.source_round, checkpoint_digest: row.checkpoint_digest,
    snapshot_id: row.report.snapshot_id, sequence: row.report.sequence };
}
const hash = (value: string | null) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export function observationReason(reason: string) {
  const labels: Record<string, string> = { checkpoint_unreadable: "活动资料损坏或不可读取", checkpoint_missing: "活动检查点已不存在",
    checkpoint_changed: "活动版本已变化", checkpoint_save_failed: "本次观察保存失败", checkpoint_byte_budget: "达到本次资料读取上限" };
  return labels[reason] || "观察未确认";
}
export function observationMatches(result: TaskCoverageObservation, records: TaskCoverageRecords,
  rows: TaskCoverageRecords["active"], request: string) {
  if (result?.request_id !== request || result.owner_session !== records.owner_session
      || result.source_task_id !== records.source_task_id || result.workspace_id !== records.workspace_id
      || result.pages_advanced !== 0 || result.task_graph_reconciled !== false
      || result.limits?.checkpoints !== 4 || result.limits.checkpoint_bytes !== 16777216 || result.limits.inventory_entries !== 8192
      || !Number.isFinite(Date.parse(result.observed_at)) || !Array.isArray(result.results)
      || result.results.length !== rows.length || rows.length < 1 || rows.length > 4 || result.requested !== rows.length
      || !Number.isInteger(result.checkpoint_bytes_read) || result.checkpoint_bytes_read < 0 || result.checkpoint_bytes_read > 16777217
      || !result.namespace || typeof result.namespace.stable !== "boolean"
      || typeof result.namespace.reason !== "string"
      || [result.namespace.before_digest, result.namespace.after_digest].some(value => value !== null && !hash(value))
      || result.namespace.stable && (!hash(result.namespace.before_digest) || result.namespace.before_digest !== result.namespace.after_digest)) return false;
  let observed = 0;
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i], item = result.results[i], ref = observationRef(row), r = item.reference;
    if (!r || Object.keys(ref).some(key => r[key as keyof typeof ref] !== ref[key as keyof typeof ref])) return false;
    if (item.state === "not_observed") {
      if (item.persisted !== false || !["checkpoint_unreadable", "checkpoint_missing", "checkpoint_changed",
        "checkpoint_save_failed", "checkpoint_byte_budget"].includes(item.reason) || "report" in item) return false;
      continue;
    }
    if (item.state !== "observed" || item.persisted !== true || item.reason !== "" || !hash(item.checkpoint_digest)
        || !coverageMatches(item.report, records.source_task_id, ref.round, row.report.request_id, row.report.mode)) return false;
    const previous = row.report, current = item.report;
    if (current.scan_id !== ref.scan_id || current.snapshot_id !== ref.snapshot_id || current.sequence !== ref.sequence
        || current.evidence_digest !== previous.evidence_digest || current.observed_at !== result.observed_at
        || current.coverage.entries_read !== previous.coverage.entries_read || current.coverage.entries_total !== previous.coverage.entries_total
        || current.page_size !== previous.page_size || current.coverage.processed !== previous.coverage.processed
        || ["record_bytes", "skipped", "damaged", "graph_complete", "edge_visits", "selected", "reconciled", "failures", "covered_digest",
          "record_range", "task_range"].some(key => JSON.stringify(current.coverage[key as keyof typeof current.coverage])
            !== JSON.stringify(previous.coverage[key as keyof typeof previous.coverage]))
        || current.snapshot_valid && (current.phase !== previous.phase || current.next_cursor !== previous.next_cursor || current.complete !== previous.complete)
        || !previous.snapshot_valid && current.snapshot_valid || !result.namespace.stable && current.snapshot_valid) return false;
    observed++;
  }
  return result.observed === observed && result.unobserved === rows.length - observed
    && result.observation_state === (observed === rows.length ? "observed" : observed ? "partial" : "unknown");
}
