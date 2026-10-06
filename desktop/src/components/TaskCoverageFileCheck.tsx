import { useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { TaskCoverageArchive, TaskCoverageFileVerification, TaskCoveragePreservationVerification } from "../types";
import { MAX_COVERAGE_FILE_BYTES, MAX_PRESERVATION_BYTES, coverageFileMatches, prepareCoverageFile,
  preparePreservationReceipt, preservationMatches } from "../lib/taskCoverageFile";

type Props = { archive: TaskCoverageArchive; session: string; client: GatewayClient | null; busy: boolean;
  run: (operation: (client: GatewayClient, current: () => boolean) => Promise<void>) => Promise<void> };
export function TaskCoverageFileCheck({ archive, session, client, busy, run }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [receipt, setReceipt] = useState<TaskCoverageFileVerification | null>(null);
  const [preservationFile, setPreservationFile] = useState<File | null>(null);
  const [preservation, setPreservation] = useState<TaskCoveragePreservationVerification | null>(null);
  const version = useRef(0), input = useRef<HTMLInputElement>(null), preservationInput = useRef<HTMLInputElement>(null);
  useEffect(() => { version.current++; setFile(null); setPreservationFile(null); setReceipt(null); setPreservation(null);
    if (input.current) input.current.value = ""; if (preservationInput.current) preservationInput.current.value = "";
  }, [client, archive.archive_digest]);
  function clearResults() { version.current++; setReceipt(null); setPreservation(null); }
  function verify(withPreservation = false) {
    if (!file || withPreservation && !preservationFile) return;
    const generation = version.current;
    void run(async (c, current) => {
      setReceipt(null); setPreservation(null);
      if (!file.size || file.size > MAX_COVERAGE_FILE_BYTES) throw new Error("历史文件为空或超过 8454144 字节上限；未确认文件。");
      const prepared = await prepareCoverageFile(new Uint8Array(await file.arrayBuffer()), archive);
      if (!current() || version.current !== generation) return;
      if (withPreservation && preservationFile) {
        if (!preservationFile.size || preservationFile.size > MAX_PRESERVATION_BYTES) throw new Error("保存凭据为空或超过 65536 字节上限。");
        const saved = await preparePreservationReceipt(new Uint8Array(await preservationFile.arrayBuffer()), archive, prepared.fileDigest);
        if (!current() || version.current !== generation) return;
        const result = await c.verifyTaskCoveragePreservation(archive.source_task_id, session, archive, prepared.raw,
          prepared.fileDigest, saved.raw, saved.receiptFileDigest);
        if (!current() || version.current !== generation) return;
        if (!preservationMatches(result, archive, prepared.fileDigest, prepared.fileBytes, saved)) throw new Error("保存凭据响应不匹配；状态未知。");
        setReceipt(result); setPreservation(result); return;
      }
      const result = await c.verifyTaskCoverageFile(archive.source_task_id, session, archive, prepared.raw, prepared.fileDigest);
      if (!current() || version.current !== generation) return;
      if (!coverageFileMatches(result, archive, prepared.fileDigest, prepared.fileBytes)) throw new Error("文件回验响应不匹配；状态未知。");
      setReceipt(result);
    });
  }
  return <details className="task-coverage-file-check" style={{ overflowWrap: "anywhere" }}>
    <summary>回验导出的历史文件</summary>
    <label>选择导出的历史 JSON 回验<input ref={input} type="file" accept=".json,application/json" disabled={!client || busy}
      aria-label={`选择回验文件 ${archive.archive_id}`} style={{ maxWidth: "100%" }}
      onChange={e => { clearResults(); setFile(e.target.files?.[0] ?? null); }} /></label>
    <div className="task-actions"><button disabled={!client || busy || !file} onClick={() => verify()}>核验所选文件</button></div>
    <p>本地保留命令会同步保存原文并生成 preservation.json。选择凭据仅核对内容，不访问记录的保存位置。</p>
    <label>选择保存凭据<input ref={preservationInput} type="file" accept=".json,application/json" disabled={!client || busy}
      aria-label={`选择保存凭据 ${archive.archive_id}`} style={{ maxWidth: "100%" }}
      onChange={e => { clearResults(); setPreservationFile(e.target.files?.[0] ?? null); }} /></label>
    <div className="task-actions"><button disabled={!client || busy || !file || !preservationFile} onClick={() => verify(true)}>核对保存凭据与原文</button></div>
    {receipt && <p role="status">所选文件的摘要与收据链一致 · {receipt.file_bytes} 字节 · {receipt.verified_at}<br />
      原文 SHA-256：<code>{receipt.file_digest}</code><br />
      仅核验此次读取的文件；不证明外部持久保存或当前覆盖，不释放容量。</p>}
    {preservation && <p role="status" className="task-coverage-preservation-result">保存凭据与所选原文一致 · 保存记录时间 {preservation.preservation.preserved_at}<br />
      保存位置（记录值）：{preservation.preservation.storage.directory}<br />
      服务器未读取该位置；不证明独立故障域或未来保留，不释放历史容量。</p>}
  </details>;
}
