import type { TaskCoverageArchive, TaskCoverageRecords, TaskCoverageRetirement } from "../types";
const hash = (s: string) => typeof s === "string" && /^[a-f0-9]{64}$/.test(s);
const count = (n: number, max: number) => Number.isInteger(n) && n >= 0 && n <= max;
const time = (s: string) => typeof s === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(s) && Number.isFinite(Date.parse(s));
export function retirementMatches(r: TaskCoverageRetirement, a: Pick<TaskCoverageArchive, "scan_id" | "owner_session" | "source_task_id" | "source_round" | "workspace_id">) {
  const e = r?.artifact?.evidence, c = r?.coverage;
  const archive = "report" in a ? a as TaskCoverageArchive : null;
  return r?.state === "retired" && r.verified === true && r.integrity === "sha256_and_identity_binding"
    && r.historical === true && r.current_coverage === false && r.resumable === false
    && r.history_capacity_released === false && r.durable_copy_confirmed === false
    && r.scan_id === a.scan_id && r.owner_session === a.owner_session && r.source_task_id === a.source_task_id
    && r.source_round === a.source_round && r.workspace_id === a.workspace_id && hash(r.workspace_id)
    && /^scan-[a-f0-9]{24}$/.test(r.scan_id) && r.retirement_id === `retired-${r.scan_id.slice(5)}` && hash(r.retirement_digest)
    && /^archive-[a-f0-9]{24}-[a-f0-9]{24}$/.test(r.archive_id) && hash(r.archive_digest) && hash(r.checkpoint_digest)
    && count(r.source_round, 3) && r.source_round > 0 && /^(?:discover|reconcile)$/.test(r.mode)
    && count(r.page_size, 512) && r.page_size > 0 && /^[A-Za-z0-9_.-]{1,120}$/.test(r.scan_request_id)
    && time(r.retired_at) && time(r.archived_at)
    && ["explicit_active_checkpoint_release", "explicit_legacy_identity_retirement"].includes(r.reason)
    && hash(c?.snapshot_id) && hash(c?.evidence_digest) && count(c?.sequence, 1025) && c.receipt_count === c.sequence
    && time(c.observed_at) && ["complete", "incomplete", "unknown"].includes(c.state)
    && typeof c.complete === "boolean" && typeof c.snapshot_valid === "boolean"
    && (c.state === "complete") === c.complete && (!c.complete || c.snapshot_valid)
    && Array.isArray(c.reasons) && c.reasons.length <= 16 && c.reasons.every(reason => typeof reason === "string")
    && r.artifact.kind === "task_coverage_retirement" && r.artifact.version === 1
    && r.artifact.retirement_id === r.retirement_id && r.artifact.sha256 === r.retirement_digest
    && e?.workspace_id === r.workspace_id && e.scan_id === r.scan_id && e.archive_id === r.archive_id
    && e.archive_digest === r.archive_digest && e.checkpoint_digest === r.checkpoint_digest
    && e.archived_at === r.archived_at && e.retired_at === r.retired_at && e.reason === r.reason
    && JSON.stringify(e.scope) === JSON.stringify([r.owner_session, r.source_task_id, r.source_round, r.scan_request_id, r.mode, r.page_size])
    && (!archive || r.scan_request_id === archive.report.request_id && r.mode === archive.report.mode && r.page_size === archive.report.page_size)
    && (!archive || r.archive_id !== archive.archive_id || r.archive_digest === archive.archive_digest
      && r.checkpoint_digest === archive.checkpoint_digest && r.archived_at === archive.archived_at
      && c.snapshot_id === archive.report.snapshot_id && c.evidence_digest === archive.report.evidence_digest
      && c.sequence === archive.report.sequence && c.receipt_count === archive.report.receipt_count && c.observed_at === archive.report.observed_at)
    && e.coverage !== null && typeof e.coverage === "object" && Object.keys(c).length === 9 && Object.keys(e.coverage).length === 9
    && Object.keys(c).every(key => JSON.stringify(c[key as keyof typeof c]) === JSON.stringify(e.coverage[key as keyof typeof c]));
}
export function archiveMatches(a: TaskCoverageArchive, source: string, owner: string, workspace?: string) {
  return a?.historical === true && a.current_coverage === false && a.resumable === false && a.verified === true
    && a.integrity === "sha256_and_receipt_chain" && a.source_task_id === source && a.owner_session === owner
    && (!workspace || a.workspace_id === workspace) && hash(a.workspace_id) && hash(a.archive_digest) && hash(a.checkpoint_digest)
    && /^archive-[a-f0-9]{24}-[a-f0-9]{24}$/.test(a.archive_id) && /^scan-[a-f0-9]{24}$/.test(a.scan_id)
    && Number.isInteger(a.source_round) && a.source_round >= 1 && a.source_round <= 3
    && Number.isFinite(Date.parse(a.archived_at)) && a.report?.source_task_id === source
    && a.report.source_round === a.source_round && a.report.scan_id === a.scan_id && a.report.next_cursor === null
    && hash(a.report.snapshot_id) && hash(a.report.evidence_digest) && a.report.receipt_count === a.report.sequence;
}
export function recordsMatch(r: TaskCoverageRecords, source: string, owner: string, workspace?: string) {
  const c = r?.capacity;
  return r?.source_task_id === source && r.owner_session === owner && hash(r.workspace_id)
    && (!workspace || r.workspace_id === workspace) && c?.active_limit === 32 && c.archives_limit === 128
    && c.retired_limit === 128 && count(c.retired_used, 128) && count(c.unreadable_retired, c.retired_used)
    && count(c.active_used, 32) && count(c.archives_used, 128) && count(c.unreadable_active, c.active_used)
    && Array.isArray(r.active) && r.active.length <= c.active_used && r.active.every(row => row.historical === false
      && row.owner_session === owner && hash(row.checkpoint_digest) && row.report.source_task_id === source
      && /^scan-[a-f0-9]{24}$/.test(row.report.scan_id) && count(row.report.source_round, 3) && row.report.source_round > 0
      && (row.retirement === undefined || row.retirement.state === "not_recorded" || row.retirement.state === "unknown" || row.retirement.state === "retired" && retirementMatches(row.retirement,
        { scan_id: row.report.scan_id, owner_session: owner, source_task_id: source, source_round: row.report.source_round, workspace_id: r.workspace_id })))
    && Array.isArray(r.archives) && r.archives.length <= 16 && r.archives.every(row => row.verified
      ? archiveMatches(row, source, owner, r.workspace_id) && (row.retirement === undefined || row.retirement.state === "not_recorded" || row.retirement.state === "unknown"
        || row.retirement.state === "retired" && retirementMatches(row.retirement, row)) : row.historical === true && row.current_coverage === false
        && row.state === "unknown" && /^archive-[a-f0-9]{24}-[a-f0-9]{24}$/.test(row.archive_id))
    && count(r.offset, 128) && count(r.archive_count, 128)
    && (r.next_offset === null || count(r.next_offset, 128) && r.next_offset === r.offset + 16)
    && Array.isArray(r.retirements) && r.retirements.length <= 16 && count(r.retirement_count, c.retired_used)
    && count(r.retirement_offset, 128) && (r.next_retirement_offset === null || count(r.next_retirement_offset, 128)
      && r.next_retirement_offset === r.retirement_offset + 16)
    && r.retirements.every(row => retirementMatches(row, { scan_id: row.scan_id, owner_session: owner,
      source_task_id: source, source_round: row.source_round, workspace_id: r.workspace_id }));
}
