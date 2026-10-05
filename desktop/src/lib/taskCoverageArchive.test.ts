import { describe, it, expect } from "vitest";
import type { TaskCoverageArchive, TaskCoverageRecords, TaskCoverageRetirement } from "../types";
import { archiveMatches, recordsMatch, retirementMatches } from "./taskCoverageArchive";
function archive(): TaskCoverageArchive {
  return { archive_id: `archive-${"a".repeat(24)}-${"b".repeat(24)}`, scan_id: `scan-${"c".repeat(24)}`,
    archive_digest: "a".repeat(64), checkpoint_digest: "b".repeat(64), workspace_id: "c".repeat(64),
    owner_session: "sid-owner", source_task_id: "task-source", source_round: 1, request_id: "request",
    historical: true, current_coverage: false, resumable: false, verified: true, replayed: false,
    integrity: "sha256_and_receipt_chain", archived_at: "2026-10-04T12:00:00+00:00",
    report: { source_task_id: "task-source", source_round: 1, scan_id: `scan-${"c".repeat(24)}`, next_cursor: null,
      request_id: "coverage-1", mode: "reconcile", page_size: 512, observed_at: "2026-10-04T12:00:00+00:00",
      snapshot_id: "d".repeat(64), evidence_digest: "e".repeat(64), receipt_count: 5, sequence: 5 } as TaskCoverageArchive["report"] };
}
function records(): TaskCoverageRecords {
  return { owner_session: "sid-owner", source_task_id: "task-source", workspace_id: "c".repeat(64),
    capacity: { active_used: 32, active_limit: 32, archives_used: 1, archives_limit: 128, unreadable_active: 1, retired_used: 0, retired_limit: 128, unreadable_retired: 0 },
    active: [], archives: [archive()], offset: 0, next_offset: null, archive_count: 1,
    retirements: [], retirement_offset: 0, next_retirement_offset: null, retirement_count: 0 };
}
function retirement(): TaskCoverageRetirement {
  const a = archive();
  const coverage: TaskCoverageRetirement["coverage"] = { snapshot_id: "d".repeat(64), evidence_digest: "e".repeat(64),
    sequence: 5, receipt_count: 5, observed_at: a.archived_at, state: "complete", complete: true, snapshot_valid: true, reasons: [] };
  const evidence: TaskCoverageRetirement["artifact"]["evidence"] = { workspace_id: a.workspace_id, scan_id: a.scan_id,
    scope: [a.owner_session, a.source_task_id, a.source_round, "coverage-1", "reconcile", 512], archive_id: a.archive_id,
    archive_digest: a.archive_digest, checkpoint_digest: a.checkpoint_digest, archived_at: a.archived_at,
    retired_at: a.archived_at, reason: "explicit_active_checkpoint_release", coverage };
  return { state: "retired", retirement_id: `retired-${a.scan_id.slice(5)}`, retirement_digest: "f".repeat(64),
    workspace_id: a.workspace_id, owner_session: a.owner_session, source_task_id: a.source_task_id, source_round: 1,
    scan_id: a.scan_id, scan_request_id: "coverage-1", mode: "reconcile", page_size: 512, archive_id: a.archive_id,
    archive_digest: a.archive_digest, checkpoint_digest: a.checkpoint_digest, archived_at: a.archived_at,
    retired_at: a.archived_at, reason: evidence.reason, coverage, historical: true, current_coverage: false,
    resumable: false, verified: true, integrity: "sha256_and_identity_binding", history_capacity_released: false, durable_copy_confirmed: false,
    artifact: { version: 1, kind: "task_coverage_retirement", retirement_id: `retired-${a.scan_id.slice(5)}`, sha256: "f".repeat(64), evidence } };
}
describe("coverage retention contracts", () => {
  it("recognizes verified evidence only as historical", () => {
    const a = archive(); expect(archiveMatches(a, "task-source", "sid-owner")).toBe(true);
    a.report.complete = true; expect(archiveMatches(a, "task-source", "sid-owner")).toBe(true);
    a.current_coverage = true as false; expect(archiveMatches(a, "task-source", "sid-owner")).toBe(false);
  });
  it.each(["source", "owner", "workspace", "round", "cursor", "chain", "identity"])("rejects wrong %s", key => {
    const a = archive();
    if (key === "source") a.source_task_id = "task-other";
    if (key === "owner") a.owner_session = "sid-other";
    if (key === "workspace") a.workspace_id = "f".repeat(64);
    if (key === "round") a.source_round = 2;
    if (key === "cursor") a.report.next_cursor = "old";
    if (key === "chain") a.report.receipt_count = 4;
    if (key === "identity") a.archive_id = "archive-wrong";
    expect(archiveMatches(a, "task-source", "sid-owner", "c".repeat(64))).toBe(false);
  });
  it("keeps unreadable records counted and cannot enlarge quotas", () => {
    const r = records(); expect(recordsMatch(r, "task-source", "sid-owner")).toBe(true);
    r.capacity.active_limit = 33; expect(recordsMatch(r, "task-source", "sid-owner")).toBe(false);
    r.capacity.active_limit = 32; r.capacity.active_used = 33;
    expect(recordsMatch(r, "task-source", "sid-owner")).toBe(false);
  });
  it("admits damaged historical entries only as unknown", () => {
    const r = records(); r.archives = [{ archive_id: archive().archive_id, historical: true, current_coverage: false, verified: false, state: "unknown" }];
    expect(recordsMatch(r, "task-source", "sid-owner")).toBe(true);
    r.archives[0].current_coverage = true as false;
    expect(recordsMatch(r, "task-source", "sid-owner")).toBe(false);
  });
  it("bounds historical navigation without combining page coverage", () => {
    const r = records(); r.next_offset = 16;
    expect(recordsMatch(r, "task-source", "sid-owner")).toBe(true);
    r.next_offset = 1; expect(recordsMatch(r, "task-source", "sid-owner")).toBe(false);
  });
  it("keeps independently listed retirement historical when its archive is absent", () => {
    const r = records(); r.archives = []; r.archive_count = 0;
    r.retirements = [retirement()]; r.retirement_count = 1; r.capacity.retired_used = 1;
    expect(recordsMatch(r, "task-source", "sid-owner")).toBe(true);
    expect(retirementMatches(r.retirements[0], archive())).toBe(true);
  });
  it.each(["owner", "round", "workspace", "snapshot", "bound_snapshot", "summary", "resumable", "durability", "release", "time", "scope", "digest", "receipt", "budget", "state"])("rejects retirement %s mismatch", key => {
    const r = structuredClone(retirement());
    if (key === "owner") r.owner_session = "sid-other";
    if (key === "round") r.source_round = 2;
    if (key === "workspace") r.workspace_id = "f".repeat(64);
    if (key === "snapshot") r.coverage.snapshot_id = "x";
    if (key === "bound_snapshot") r.coverage.snapshot_id = "f".repeat(64);
    if (key === "summary") r.artifact.evidence.coverage = null as unknown as typeof r.coverage;
    if (key === "resumable") r.resumable = true as false;
    if (key === "durability") r.durable_copy_confirmed = true as false;
    if (key === "release") r.history_capacity_released = true as false;
    if (key === "time") r.retired_at = "2026-10-04T12:00:00";
    if (key === "scope") r.artifact.evidence.scope[3] = "changed-request";
    if (key === "digest") r.artifact.sha256 = "a".repeat(64);
    if (key === "receipt") r.coverage.receipt_count = 4;
    if (key === "budget") r.page_size = 513;
    if (key === "state") r.coverage.state = "incomplete";
    expect(retirementMatches(r, archive())).toBe(false);
  });
  it("bounds retirement quota and pages independently of archive pages", () => {
    const r = records(); r.next_retirement_offset = 16;
    expect(recordsMatch(r, "task-source", "sid-owner")).toBe(true);
    r.next_retirement_offset = 1; expect(recordsMatch(r, "task-source", "sid-owner")).toBe(false);
    r.next_retirement_offset = null; r.capacity.retired_limit = 129;
    expect(recordsMatch(r, "task-source", "sid-owner")).toBe(false);
  });
  it("rejects unrecognized retirement states instead of treating them as absent", () => {
    const r = records(); (r.archives[0] as TaskCoverageArchive).retirement = { state: "current" } as unknown as TaskCoverageArchive["retirement"];
    expect(recordsMatch(r, "task-source", "sid-owner")).toBe(false);
  });
});
