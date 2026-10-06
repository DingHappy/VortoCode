import { describe, it, expect } from "vitest";
import type { TaskCoverageReport } from "../types";
import { coverageMatches, coverageLabel } from "./taskCoverage";

function partial(): TaskCoverageReport {
  const scan = `scan-${"a".repeat(24)}`;
  return { scan_id: scan, request_id: "request", source_task_id: "task-source", source_round: 1,
    mode: "reconcile", sequence: 1, phase: "records", page_size: 512, complete: false, page_complete: true,
    replayed: false, snapshot_valid: true, next_cursor: `${scan}.1.${"b".repeat(64)}`,
    observed_at: "2026-10-04T12:30:00.123456+00:00", order: "filename_utf8_bytes_ascending",
    snapshot_id: "a".repeat(64), evidence_digest: "b".repeat(64), receipt_count: 1,
    validity: "observed_namespace_and_lstat", tasks: [], errors: [],
    coverage: { state: "incomplete", entries_total: 600, entries_read: 512, record_bytes: 0, skipped: 0,
      damaged: 0, graph_complete: false, edge_visits: 0, selected: 0, processed: 0, reconciled: 0,
      failures: 0, reasons: [], record_range: [0, 512], task_range: [0, 0], covered_digest: "c".repeat(64) } };
}
function completed() {
  const r = partial();
  r.complete = true; r.phase = "finished"; r.next_cursor = null;
  r.coverage.state = "complete"; r.coverage.entries_read = 600; r.coverage.graph_complete = true;
  r.coverage.selected = r.coverage.processed = r.coverage.reconciled = 70;
  r.coverage.record_range = [0, 600]; r.coverage.task_range = [0, 70];
  return r;
}
describe("task coverage contract", () => {
  it("a completed page is still incomplete workspace and graph coverage", () => {
    const r = partial();
    expect(coverageMatches(r, "task-source", 1, "request")).toBe(true);
    expect(coverageLabel(r)).toBe("覆盖核对未完成");
    r.complete = true;
    expect(coverageMatches(r, "task-source", 1)).toBe(false);
  });
  it("labels completion as a previous observation, without accepting task results", () => {
    const r = completed();
    expect(coverageMatches(r, "task-source", 1)).toBe(true);
    expect(coverageLabel(r)).toBe("上次覆盖核对已完成");
  });
  it.each(["damaged", "skipped", "failures"] as const)("rejects complete with %s", key => {
    const r = completed(); r.coverage[key] = 1;
    expect(coverageMatches(r, "task-source", 1)).toBe(false);
  });
  it("accepts an unknown invalidated snapshot without a resume cursor", () => {
    const r = partial(); r.snapshot_valid = false; r.next_cursor = null;
    r.coverage.state = "unknown"; r.phase = "finished"; r.coverage.reasons = ["snapshot_changed"];
    expect(coverageMatches(r, "task-source", 1)).toBe(true);
    expect(coverageLabel(r)).toBe("覆盖状态未知");
  });
  it("rejects wrong source, round, request and stale cursor", () => {
    const r = partial();
    expect(coverageMatches(r, "task-other", 1)).toBe(false);
    expect(coverageMatches(r, "task-source", 2)).toBe(false);
    expect(coverageMatches(r, "task-source", 1, "other")).toBe(false);
    r.next_cursor = r.next_cursor!.replace(".1.", ".0.");
    expect(coverageMatches(r, "task-source", 1)).toBe(false);
  });
  it.each([-1, 0.5, Number.NaN, 700])("rejects impossible cumulative entry counts %s", count => {
    const r = partial(); r.coverage.entries_read = count;
    expect(coverageMatches(r, "task-source", 1)).toBe(false);
  });
  it("rejects graph or process gaps hidden behind a success flag", () => {
    const r = completed(); r.coverage.processed = 69; r.coverage.reconciled = 69;
    expect(coverageMatches(r, "task-source", 1)).toBe(false);
    r.coverage.processed = r.coverage.reconciled = 70; r.coverage.graph_complete = false;
    expect(coverageMatches(r, "task-source", 1)).toBe(false);
  });
});
