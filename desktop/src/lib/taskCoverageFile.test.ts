import { describe, expect, it } from "vitest";
import type { TaskCoverageArchive, TaskCoverageFileVerification } from "../types";
import { MAX_COVERAGE_FILE_BYTES, MAX_PRESERVATION_BYTES, coverageFileMatches, prepareCoverageFile,
  preparePreservationReceipt, preservationMatches } from "./taskCoverageFile";

function archive(): TaskCoverageArchive {
  return { archive_id: `archive-${"a".repeat(24)}-${"b".repeat(24)}`, scan_id: `scan-${"c".repeat(24)}`,
    archive_digest: "a".repeat(64), checkpoint_digest: "b".repeat(64), workspace_id: "c".repeat(64),
    owner_session: "sid-owner", source_task_id: "task-source", source_round: 1, request_id: "request",
    historical: true, current_coverage: false, resumable: false, verified: true, replayed: false,
    integrity: "sha256_and_receipt_chain", archived_at: "2026-10-04T12:00:00+00:00",
    report: { source_task_id: "task-source", source_round: 1, scan_id: `scan-${"c".repeat(24)}`, next_cursor: null,
      snapshot_id: "d".repeat(64), evidence_digest: "e".repeat(64), receipt_count: 5, sequence: 5 } as TaskCoverageArchive["report"] };
}
function receipt(a: TaskCoverageArchive): TaskCoverageFileVerification {
  return { ...a, file_digest: "f".repeat(64), file_bytes: 123, verified_at: "2026-10-04T13:00:00Z",
    verification: "uploaded_or_read_bytes", expected_binding_checked: ["archive_digest", "archive_id", "file_digest", "owner_session", "source_round", "source_task_id", "workspace_id"],
    durable_copy_confirmed: false, capacity_released: false, imported: false };
}
describe("portable historical file verification", () => {
  it("keeps raw unsafe integers and UTF-8 bytes while computing the original file digest", async () => {
    const a = archive();
    const raw = `{"archive_id":"${a.archive_id}","sha256":"${a.archive_digest}","integer":1791112345678901234,"note":"历史证据"}`;
    const bytes = new TextEncoder().encode(raw), prepared = await prepareCoverageFile(bytes, a);
    expect(prepared.raw).toBe(raw); expect(prepared.fileBytes).toBe(bytes.length);
    // Independently calculated with Python hashlib over the exact UTF-8 fixture.
    expect(prepared.fileDigest).toBe("f801dacaf716f997ee8ce35d307b4a894b8e236c2cf0ae19274bec124f8a6243");
  });
  it.each(["empty", "oversize", "invalid_utf8", "bom", "json", "wrong_identity", "wrong_digest"])("rejects %s before sending", async damage => {
    const a = archive(); let bytes = new TextEncoder().encode(JSON.stringify({ archive_id: a.archive_id, sha256: a.archive_digest }));
    if (damage === "empty") bytes = new Uint8Array();
    if (damage === "oversize") bytes = new Uint8Array(MAX_COVERAGE_FILE_BYTES + 1);
    if (damage === "invalid_utf8") bytes = new Uint8Array([255]);
    if (damage === "bom") bytes = new Uint8Array([239, 187, 191, ...bytes]);
    if (damage === "json") bytes = new TextEncoder().encode("null");
    if (damage === "wrong_identity") a.archive_id = "archive-other";
    if (damage === "wrong_digest") a.archive_digest = "b".repeat(64);
    await expect(prepareCoverageFile(bytes, a)).rejects.toThrow();
  });
  it("accepts only a historical, fully bound byte verification without retention claims", () => {
    const a = archive(), r = receipt(a);
    expect(coverageFileMatches(r, a, r.file_digest, 123)).toBe(true);
    r.durable_copy_confirmed = true as false;
    expect(coverageFileMatches(r, a, r.file_digest, 123)).toBe(false);
  });
  it.each(["owner", "workspace", "archive", "round", "bytes", "digest", "bindings", "current", "released", "imported", "time", "snapshot", "chain", "sequence", "archived_at", "request"])(
    "rejects wrong %s receipt", key => {
      const a = archive(), r = receipt(a);
      if (key === "owner") r.owner_session = "sid-other";
      if (key === "workspace") r.workspace_id = "f".repeat(64);
      if (key === "archive") r.archive_digest = "f".repeat(64);
      if (key === "round") r.source_round = 2;
      if (key === "bytes") r.file_bytes++;
      if (key === "digest") r.file_digest = "a".repeat(64);
      if (key === "bindings") r.expected_binding_checked = [];
      if (key === "current") r.current_coverage = true as false;
      if (key === "released") r.capacity_released = true as false;
      if (key === "imported") r.imported = true as false;
      if (key === "time") r.verified_at = "bad";
      // Keep the expected archive separate from the response report.
      r.report = { ...r.report };
      if (key === "snapshot") r.report.snapshot_id = "f".repeat(64);
      if (key === "chain") r.report.evidence_digest = "f".repeat(64);
      if (key === "sequence") { r.report.sequence++; r.report.receipt_count++; }
      if (key === "archived_at") r.archived_at = "2026-10-04T13:00:00Z";
      if (key === "request") r.request_id = "other";
      expect(coverageFileMatches(r, a, "f".repeat(64), 123)).toBe(false);
    });
});

function preservation(a: TaskCoverageArchive) {
  return { kind: "task_coverage_preservation", version: 1, receipt_id: `preserve-${"a".repeat(24)}`, sha256: "a".repeat(64),
    evidence: { archive_id: a.archive_id, archive_digest: a.archive_digest, file_digest: "f".repeat(64),
      preserved_at: "2026-10-04T13:00:00Z", storage: { directory: "/private/tmp/selected-store", artifact_name: "selected.archive.json",
        directory_device: "16777230", directory_inode: "1791112345678901234", artifact_inode: "1791112345678901235",
        method: "local_file_fsync_directory_fsync_read_back" as const } } };
}
describe("local preservation receipt", () => {
  it("keeps both original texts and storage identities beyond Number precision", async () => {
    const a = archive(), raw = JSON.stringify(preservation(a));
    const prepared = await preparePreservationReceipt(new TextEncoder().encode(raw), a, "f".repeat(64));
    expect(prepared.raw).toBe(raw); expect(prepared.storage.artifact_inode).toBe("1791112345678901235");
    expect(prepared.receiptFileDigest).toMatch(/^[a-f0-9]{64}$/);
  });
  it.each(["empty", "oversize", "utf8", "bom", "json", "wrong_file", "wrong_archive"])("rejects %s receipt", async key => {
    const a = archive(), r = preservation(a);
    if (key === "wrong_file") r.evidence.file_digest = "b".repeat(64);
    if (key === "wrong_archive") r.evidence.archive_id = "wrong";
    let bytes = new TextEncoder().encode(JSON.stringify(r));
    if (key === "empty") bytes = new Uint8Array();
    if (key === "oversize") bytes = new Uint8Array(MAX_PRESERVATION_BYTES + 1);
    if (key === "utf8") bytes = new Uint8Array([255]);
    if (key === "bom") bytes = new Uint8Array([239,187,191,...bytes]);
    if (key === "json") bytes = new TextEncoder().encode("null");
    await expect(preparePreservationReceipt(bytes,a,"f".repeat(64))).rejects.toThrow();
  });
  it.each(["valid", "receipt", "raw_digest", "time", "directory", "inode", "state", "observed", "failure_domain", "snapshot"])(
    "checks %s response without trusting a storage claim", async key => {
      const a = archive(), r = preservation(a), prepared = await preparePreservationReceipt(new TextEncoder().encode(JSON.stringify(r)), a, "f".repeat(64));
      const result: import("../types").TaskCoveragePreservationVerification = { ...receipt(a), preservation: {
        receipt_id: prepared.receiptId, receipt_digest: prepared.receiptDigest, receipt_file_digest: prepared.receiptFileDigest,
        preserved_at: prepared.preservedAt, storage: { ...prepared.storage }, state: "receipt_and_uploaded_bytes_consistent",
        storage_observed_now: false, independent_failure_domain_confirmed: false } };
      if (key === "receipt") result.preservation.receipt_id = "wrong";
      if (key === "raw_digest") result.preservation.receipt_file_digest = "a".repeat(64);
      if (key === "time") result.preservation.preserved_at = "2026-10-05T13:00:00Z";
      if (key === "directory") result.preservation.storage.directory = "/other";
      if (key === "inode") result.preservation.storage.artifact_inode = "123";
      if (key === "state") result.preservation.state = "synced_and_read_back" as typeof result.preservation.state;
      if (key === "observed") result.preservation.storage_observed_now = true as false;
      if (key === "failure_domain") result.preservation.independent_failure_domain_confirmed = true as false;
      if (key === "snapshot") result.report = { ...result.report, snapshot_id: "f".repeat(64) };
      expect(preservationMatches(result,a,"f".repeat(64),123,prepared)).toBe(key === "valid");
    });
});
