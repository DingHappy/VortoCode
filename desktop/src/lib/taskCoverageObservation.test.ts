import { describe, expect, it } from "vitest";
import type { TaskCoverageObservation, TaskCoverageRecords } from "../types";
import { observationMatches, observationRef } from "./taskCoverageObservation";

function fixture() {
  const scan = `scan-${"a".repeat(24)}`, hash = "b".repeat(64);
  const records: TaskCoverageRecords = { owner_session: "sid-owner", source_task_id: "task-source", workspace_id: hash,
    capacity: { active_used: 2, active_limit: 32, archives_used: 0, archives_limit: 128, unreadable_active: 0, retired_used: 0, retired_limit: 128, unreadable_retired: 0 },
    retirements: [], retirement_offset: 0, next_retirement_offset: null, retirement_count: 0,
    offset: 0, next_offset: null, archive_count: 0, archives: [], active: [{ historical: false, owner_session: "sid-owner",
      checkpoint_digest: hash, report: { scan_id: scan, request_id: "scan-request", source_task_id: "task-source", source_round: 1,
        mode: "reconcile", page_size: 512, sequence: 1, receipt_count: 1, phase: "records", snapshot_valid: true,
        snapshot_id: hash, evidence_digest: hash, next_cursor: `${scan}.1.${hash}`, complete: false, page_complete: true, replayed: false,
        order: "filename_utf8_bytes_ascending", validity: "observed_namespace_and_lstat", observed_at: "2026-10-04T12:00:00Z",
        tasks: [], errors: [], coverage: { state: "incomplete", entries_total: 601, entries_read: 512, record_bytes: 10,
          skipped: 0, damaged: 0, graph_complete: false, edge_visits: 0, selected: 0, processed: 0, reconciled: 0,
          failures: 0, reasons: [], record_range: [0, 512], task_range: [0, 0], covered_digest: hash } } }] };
  const result: TaskCoverageObservation = { request_id: "observe-request", owner_session: records.owner_session,
    source_task_id: records.source_task_id, workspace_id: hash, observed_at: "2026-10-04T13:00:00Z", requested: 1,
    observed: 1, unobserved: 0, observation_state: "observed", task_graph_reconciled: false, pages_advanced: 0,
    namespace: { stable: true, before_digest: hash, after_digest: hash, reason: "" }, checkpoint_bytes_read: 2000,
    limits: { checkpoints: 4, checkpoint_bytes: 16777216, inventory_entries: 8192 }, results: [{ reference: observationRef(records.active[0]),
      persisted: true, state: "observed", reason: "", checkpoint_digest: "c".repeat(64),
      report: { ...structuredClone(records.active[0].report), observed_at: "2026-10-04T13:00:00Z" } }] };
  return { records, result };
}
function matches(records: TaskCoverageRecords, result: TaskCoverageObservation) {
  return observationMatches(result, records, records.active, "observe-request");
}
describe("explicit external coverage observation", () => {
  it("an observed selection keeps incomplete coverage and its cursor", () => {
    const { records, result } = fixture();
    expect(matches(records, result)).toBe(true);
    expect(result.results[0].state === "observed" && result.results[0].report.complete).toBe(false);
    expect(records.capacity.active_used).toBe(2); // One other checkpoint remains outside this selection.
  });
  it("admits latched invalidation with the original receipt chain and no cursor", () => {
    const { records, result } = fixture(); const item = result.results[0];
    if (item.state !== "observed") throw new Error();
    item.report.snapshot_valid = false; item.report.phase = "finished"; item.report.next_cursor = null;
    item.report.coverage.state = "unknown"; item.report.coverage.reasons = ["snapshot_changed"];
    result.namespace.stable = false; result.namespace.reason = "snapshot_changed";
    expect(matches(records, result)).toBe(true);
    item.report.snapshot_valid = true; expect(matches(records, result)).toBe(false);
  });
  it.each(["source", "owner", "workspace", "request", "version", "snapshot", "round", "sequence", "progress", "chain", "pages", "time"])(
    "rejects mismatched or advancing %s", key => {
      const { records, result } = fixture(); const item = result.results[0]; if (item.state !== "observed") throw new Error();
      if (key === "source") result.source_task_id = "task-other";
      if (key === "owner") result.owner_session = "sid-other";
      if (key === "workspace") result.workspace_id = "f".repeat(64);
      if (key === "request") result.request_id = "old-request";
      if (key === "version") item.reference.checkpoint_digest = "f".repeat(64);
      if (key === "snapshot") item.report.snapshot_id = "f".repeat(64);
      if (key === "round") item.report.source_round = 2;
      if (key === "sequence") { item.report.sequence++; item.report.receipt_count++; }
      if (key === "progress") item.report.coverage.entries_read++;
      if (key === "chain") item.report.evidence_digest = "f".repeat(64);
      if (key === "pages") result.pages_advanced = 1 as 0;
      if (key === "time") item.report.observed_at = "2026-10-04T12:00:00Z";
      expect(matches(records, result)).toBe(false);
    });
  it("unconfirmed branches have no report and cannot inflate observed counts", () => {
    const { records, result } = fixture();
    result.results = [{ reference: observationRef(records.active[0]), state: "not_observed", persisted: false, reason: "checkpoint_save_failed" }];
    result.observed = 0; result.unobserved = 1; result.observation_state = "unknown";
    expect(matches(records, result)).toBe(true);
    result.observed = 1; expect(matches(records, result)).toBe(false);
  });
  it("discovery receipts remain discovery rather than being claimed as reconciled", () => {
    const { records, result } = fixture(); const item = result.results[0]; if (item.state !== "observed") throw new Error();
    records.active[0].report.mode = item.report.mode = "discover";
    expect(matches(records, result)).toBe(true);
    item.report.mode = "reconcile"; expect(matches(records, result)).toBe(false);
  });
});
