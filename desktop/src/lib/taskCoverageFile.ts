import type { TaskCoverageArchive, TaskCoverageFileVerification, TaskCoveragePreservationVerification } from "../types";
import { archiveMatches } from "./taskCoverageArchive";

export const MAX_COVERAGE_FILE_BYTES = 8454144;
export const MAX_PRESERVATION_BYTES = 65536;
async function fileHash(bytes: Uint8Array) {
  const digest = await crypto.subtle.digest("SHA-256", Uint8Array.from(bytes).buffer);
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
}
export async function prepareCoverageFile(bytes: Uint8Array, archive: TaskCoverageArchive) {
  if (!bytes.length || bytes.length > MAX_COVERAGE_FILE_BYTES) throw new Error("历史文件为空或超过 8454144 字节上限。");
  let raw: string, metadata: { archive_id?: string; sha256?: string } | null;
  try {
    raw = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
    metadata = JSON.parse(raw);
  } catch {
    throw new Error("所选文件不是有效的 UTF-8 JSON；未确认文件。");
  }
  if (raw.startsWith("\uFEFF")) throw new Error("历史 JSON 文件不能带 UTF-8 BOM。");
  if (metadata?.archive_id !== archive.archive_id || metadata?.sha256 !== archive.archive_digest)
    throw new Error("所选文件与此历史归档身份或摘要不匹配。");
  const fileDigest = await fileHash(bytes);
  return { raw, fileDigest, fileBytes: bytes.length };
}

export async function preparePreservationReceipt(bytes: Uint8Array, archive: TaskCoverageArchive, fileDigest: string) {
  if (!bytes.length || bytes.length > MAX_PRESERVATION_BYTES) throw new Error("保存凭据为空或超过 65536 字节上限。");
  let raw: string, metadata: { kind?: string; version?: number; receipt_id?: string; sha256?: string;
    evidence?: { archive_id?: string; archive_digest?: string; file_digest?: string; preserved_at?: string;
      storage?: TaskCoveragePreservationVerification["preservation"]["storage"] } } | null;
  try { raw = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes); metadata = JSON.parse(raw); }
  catch { throw new Error("保存凭据不是有效的 UTF-8 JSON。"); }
  if (raw.startsWith("\uFEFF") || metadata?.kind !== "task_coverage_preservation" || metadata.version !== 1
      || !/^preserve-[a-f0-9]{24}$/.test(metadata.receipt_id ?? "") || !/^[a-f0-9]{64}$/.test(metadata.sha256 ?? "")
      || metadata.evidence?.archive_id !== archive.archive_id || metadata.evidence.archive_digest !== archive.archive_digest
      || metadata.evidence.file_digest !== fileDigest || !metadata.evidence.storage
      || !Number.isFinite(Date.parse(metadata.evidence.preserved_at ?? ""))) throw new Error("保存凭据与所选原文身份或摘要不匹配。");
  return { raw, receiptFileDigest: await fileHash(bytes), receiptId: metadata.receipt_id!, receiptDigest: metadata.sha256!,
    preservedAt: metadata.evidence.preserved_at!, storage: metadata.evidence.storage };
}

export function preservationMatches(result: TaskCoveragePreservationVerification, archive: TaskCoverageArchive,
  fileDigest: string, fileBytes: number, prepared: Awaited<ReturnType<typeof preparePreservationReceipt>>) {
  const p = result?.preservation;
  return coverageFileMatches(result, archive, fileDigest, fileBytes) && p?.state === "receipt_and_uploaded_bytes_consistent"
    && p.storage_observed_now === false && p.independent_failure_domain_confirmed === false
    && p.receipt_id === prepared.receiptId && p.receipt_digest === prepared.receiptDigest
    && p.receipt_file_digest === prepared.receiptFileDigest && p.preserved_at === prepared.preservedAt
    && p.storage?.method === "local_file_fsync_directory_fsync_read_back"
    && ["directory", "artifact_name", "directory_device", "directory_inode", "artifact_inode", "method"].every(key =>
      p.storage[key as keyof typeof p.storage] === prepared.storage[key as keyof typeof prepared.storage]);
}

export function coverageFileMatches(result: TaskCoverageFileVerification, archive: TaskCoverageArchive, fileDigest: string, fileBytes: number) {
  return archiveMatches(result, archive.source_task_id, archive.owner_session, archive.workspace_id)
    && result.archive_id === archive.archive_id && result.archive_digest === archive.archive_digest
    && result.scan_id === archive.scan_id && result.source_round === archive.source_round && result.checkpoint_digest === archive.checkpoint_digest
    && result.report.snapshot_id === archive.report.snapshot_id && result.report.evidence_digest === archive.report.evidence_digest
    && result.report.sequence === archive.report.sequence && result.archived_at === archive.archived_at && result.request_id === archive.request_id
    && result.file_digest === fileDigest && /^[a-f0-9]{64}$/.test(result.file_digest) && result.file_bytes === fileBytes
    && Number.isInteger(fileBytes) && fileBytes > 0 && fileBytes <= MAX_COVERAGE_FILE_BYTES
    && result.verification === "uploaded_or_read_bytes" && result.durable_copy_confirmed === false
    && result.capacity_released === false && result.imported === false && !result.released
    && Number.isFinite(Date.parse(result.verified_at)) && Array.isArray(result.expected_binding_checked)
    && JSON.stringify(result.expected_binding_checked) === JSON.stringify(["archive_digest", "archive_id", "file_digest", "owner_session", "source_round", "source_task_id", "workspace_id"]);
}
