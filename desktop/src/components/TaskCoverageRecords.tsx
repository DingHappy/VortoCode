import { useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { TaskCoverageArchive, TaskCoverageReport, TaskCoverageRetirement, TaskCoverageRecords as Records } from "../types";
import { archiveMatches, recordsMatch, retirementMatches } from "../lib/taskCoverageArchive";
import { observationMatches, observationRef, observationReason } from "../lib/taskCoverageObservation";
import { TaskCoverageFileCheck } from "./TaskCoverageFileCheck";

type Operation = (client: GatewayClient, isCurrent: () => boolean) => Promise<void>;
type Props = { id: string; session: string; owner: string; client: GatewayClient | null; busy: boolean;
  run: (operation: Operation) => Promise<void>; onReleased: (scanId: string) => void;
  onObserved: (report: TaskCoverageReport) => void };
export function TaskCoverageRecords({ id, session, owner, client, busy, run, onReleased, onObserved }: Props) {
  const [records, setRecords] = useState<Records | null>(null);
  const [retained, setRetained] = useState<TaskCoverageArchive[]>([]);
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [observations, setObservations] = useState<Record<string, string>>({});
  const requests = useRef(new Map<string, string>());
  useEffect(() => { setRecords(null); setRetained([]); setMessage(""); setSelected([]); setObservations({}); requests.current.clear(); }, [client]);
  function load(offset = 0, retirementOffset = 0) {
    void run(async (c, current) => {
      const result = await c.taskCoverageRecords(id, session, offset, retirementOffset);
      if (!current()) return;
      if (!recordsMatch(result, id, owner, records?.workspace_id)) throw new Error("覆盖资料范围不匹配，状态未知。");
      setRecords(result); setMessage("");
      setSelected(result.active.filter(row => !row.retirement || row.retirement.state === "not_recorded").slice(0, 4).map(row => row.report.scan_id)); setObservations({});
      setRetained(result.archives.filter((row): row is TaskCoverageArchive => row.verified));
    });
  }
  function observe() {
    if (!records) return;
    const rows = records.active.filter(row => selected.includes(row.report.scan_id));
    const request = crypto.randomUUID();
    void run(async (c, current) => {
      const result = await c.observeTaskCoverage(id, session, records.workspace_id, request, rows.map(observationRef));
      if (!current()) return;
      if (!observationMatches(result, records, rows, request)) throw new Error("活动观察回执不匹配，状态未知；请重新加载。");
      const updates = new Map(result.results.map(item => [item.reference.scan_id, item]));
      setRecords(old => old ? { ...old, active: old.active.map(row => {
        const item = updates.get(row.report.scan_id);
        return item?.state === "observed" ? { ...row, report: item.report, checkpoint_digest: item.checkpoint_digest } : row;
      }) } : old);
      setObservations(old => ({ ...old, ...Object.fromEntries(result.results.map(item => [item.reference.scan_id,
        item.state === "observed" ? `已观察并保存：${result.observed_at}` : `本次未确认 · unknown（${observationReason(item.reason)}）；请重新加载后重试。`])) }));
      result.results.forEach(item => { if (item.state === "observed") onObserved(item.report); });
      setMessage(`本次所选 ${result.requested} 项：已观察 ${result.observed}，未确认 ${result.unobserved}。未选择的活动资料没有观察；任务图未核对，扫描页未推进。`);
    });
  }
  function retain(row: Records["active"][number]) {
    const key = `${row.report.scan_id}:${row.checkpoint_digest}`;
    if (!requests.current.has(key)) requests.current.set(key, crypto.randomUUID());
    const request = requests.current.get(key)!;
    void run(async (c, current) => {
      const a = await c.archiveTaskCoverage(id, session, row.report.source_round, row.report.scan_id, request, row.checkpoint_digest);
      if (!current()) return;
      if (!archiveMatches(a, id, owner, records?.workspace_id) || a.scan_id !== row.report.scan_id
        || a.source_round !== row.report.source_round || a.checkpoint_digest !== row.checkpoint_digest || a.request_id !== request)
        throw new Error("归档回执不匹配；未确认保留或释放。");
      setRetained(old => [...old.filter(item => item.archive_id !== a.archive_id), a]);
      setMessage("历史归档已保存并核验；活动容量尚未释放。");
      const latest = await c.taskCoverageRecords(id, session, records?.offset ?? 0, records?.retirement_offset ?? 0);
      if (!current()) return;
      if (!recordsMatch(latest, id, owner, records?.workspace_id)) throw new Error("归档后的容量状态未知，请重新加载。");
      setRecords(latest);
    });
  }
  function release(a: TaskCoverageArchive) {
    void run(async (c, current) => {
      const result = await c.releaseTaskCoverage(id, session, a);
      if (!current()) return;
      if (!archiveMatches(result, id, owner, records?.workspace_id) || result.archive_id !== a.archive_id
          || result.archive_digest !== a.archive_digest || result.checkpoint_digest !== a.checkpoint_digest || result.released !== true
          || result.retirement?.state !== "retired" || !retirementMatches(result.retirement, a)
          || records?.active.some(row => row.report.scan_id === a.scan_id) && result.retirement.checkpoint_digest !== a.checkpoint_digest)
        throw new Error("释放回执不匹配；请重新加载覆盖资料。");
      onReleased(a.scan_id);
      setMessage("退役身份已固定并核验；活动检查点已显式释放，历史归档保留。历史容量没有释放。");
      setRetained(old => old.map(item => item.scan_id === a.scan_id ? { ...item, retirement: result.retirement } : item));
      setRecords(old => old ? { ...old, active: old.active.filter(row => row.report.scan_id !== a.scan_id) } : old);
      const latest = await c.taskCoverageRecords(id, session, records?.offset ?? 0, records?.retirement_offset ?? 0);
      if (!current()) return;
      if (!recordsMatch(latest, id, owner, records?.workspace_id)) throw new Error("释放后的容量状态未知，请重新加载。");
      setRecords(latest);
    });
  }
  function verify(a: TaskCoverageArchive, download = false) {
    void run(async (c, current) => {
      const result = await c.verifyTaskCoverage(id, session, a);
      if (!current()) return;
      if (!archiveMatches(result, id, owner, records?.workspace_id) || result.archive_id !== a.archive_id
          || result.archive_digest !== a.archive_digest) throw new Error("历史归档核验不匹配，状态未知。");
      if (download) {
        const serialized = await c.exportTaskCoverage(id, session, a);
        if (!current()) return;
        const artifact = JSON.parse(serialized) as { archive_id?: string; sha256?: string };
        if (artifact.archive_id !== a.archive_id || artifact.sha256 !== a.archive_digest) throw new Error("导出资料不匹配，状态未知。");
        const url = URL.createObjectURL(new Blob([serialized], { type: "application/json" }));
        const link = document.createElement("a"); link.href = url; link.download = `${a.archive_id}.json`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      setMessage(download ? "已请求导出历史 JSON；下载不证明外部持久保存。" : "历史摘要与收据链一致；不证明当前覆盖有效。");
    });
  }
  function verifyRetirement(a: TaskCoverageArchive | TaskCoverageRetirement, download = false) {
    void run(async (c, current) => {
      const result = await c.taskCoverageRetirement(id, session, a);
      if (!current()) return;
      const expected = "retirement_digest" in a ? a.retirement_digest : a.retirement?.state === "retired" ? a.retirement.retirement_digest : undefined;
      if (!retirementMatches(result, a) || expected !== undefined && result.retirement_digest !== expected)
        throw new Error("扫描退役凭据不匹配，状态未知；请重新加载。");
      if (download) {
        // Compact record counters are bounded integers; it contains no lstat nanoseconds.
        const url = URL.createObjectURL(new Blob([JSON.stringify(result.artifact)], { type: "application/json" }));
        const link = document.createElement("a"); link.href = url; link.download = `${result.retirement_id}.json`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      setMessage(download ? "已请求导出退役 JSON；导出不证明外部持久保留或释放历史容量。"
        : "退役凭据摘要与身份一致；旧请求不能重建，不证明当前覆盖或原完整收据链。");
    });
  }
  const history = [...retained, ...(records?.archives.filter(row => !retained.some(a => a.archive_id === row.archive_id)) ?? [])];
  return <details className="task-coverage-records">
    <summary>覆盖资料与容量</summary>
    <p>先保留并核验历史归档，再显式释放活动检查点。历史资料不能恢复为当前覆盖。</p>
    <div className="task-actions"><button disabled={!client || busy} onClick={() => load()}>加载覆盖资料</button></div>
    {records && <>
      <p role="status">上次加载的容量：活动检查点 {records.capacity.active_used}/32 · 历史归档 {records.capacity.archives_used}/128 · 退役身份 {records.capacity.retired_used}/128</p>
      {records.capacity.retired_used === 128 && <p>退役身份容量已满；新身份无法安全释放，保留活动与历史文件。既有身份可按原合同重试；本轮不删除退役资料。</p>}
      {records.capacity.unreadable_retired > 0 && <p>有 {records.capacity.unreadable_retired} 项退役资料不可核验 · unknown，继续占容量；无法推断其归属或释放。</p>}
      {records.capacity.active_used === 32 && <p>活动容量已满；选择本来源的一项检查点，先保留历史，再显式释放。</p>}
      {records.capacity.unreadable_active > 0 && <p>有 {records.capacity.unreadable_active} 项活动资料不可读，继续占用容量；无法安全归档或释放。</p>}
      {records.capacity.archives_used === 128 && <p>历史容量已满；本轮不删除历史。可只读导出，长期迁移待处理。</p>}
      <p>显式选择最多 4 项观察台账目录和文件身份变化。此观察不读取整个任务图，不推进分页；结果只对应观察时刻。</p>
      <div className="task-actions"><button disabled={!client || busy || !selected.length} onClick={observe}>观察所选活动范围（{selected.length}/4）</button></div>
      <div role="region" aria-label="活动覆盖资料" style={{ maxHeight: 320, overflow: "auto" }}>
      {records.active.map(row => {
        const a = retained.find(item => item.scan_id === row.report.scan_id && item.checkpoint_digest === row.checkpoint_digest);
        return <div key={row.report.scan_id} style={{ overflowWrap: "anywhere", marginTop: 12 }}>
          <small>活动 · 第 {row.report.source_round} 轮 · {row.report.scan_id}</small>
          <label><input type="checkbox" aria-label={`选择观察 ${row.report.scan_id}`} checked={selected.includes(row.report.scan_id)}
            disabled={busy || !!row.retirement && row.retirement.state !== "not_recorded" || !selected.includes(row.report.scan_id) && selected.length >= 4}
            onChange={e => {
              const checked = e.target.checked;
              setSelected(old => checked ? old.length < 4 && !old.includes(row.report.scan_id) ? [...old, row.report.scan_id] : old
                : old.filter(value => value !== row.report.scan_id));
            }} />选择观察</label>
          <p>上次观察 {row.report.observed_at} · {row.report.coverage.state}（此列表不重新检查当前覆盖）</p>
          {row.retirement?.state === "retired" && <p>退役身份已固定，活动文件仍占容量；请按原归档重试释放，不能继续扫描。</p>}
          {row.retirement?.state === "unknown" && <p>退役凭据不可核验 · unknown；保留活动文件，不能继续扫描或安全释放。</p>}
          {observations[row.report.scan_id] && <p role="status">{observations[row.report.scan_id]}</p>}
          <div className="task-actions">
            <button disabled={!client || busy || !!a} onClick={() => retain(row)}>保留历史归档</button>
            {a && <button disabled={!client || busy} onClick={() => release(a)}>显式释放活动检查点</button>}
          </div>
        </div>;
      })}
      </div>
    </>}
    {history.map(a => <div key={a.archive_id} className="task-coverage-history" data-archive-id={a.archive_id} style={{ overflowWrap: "anywhere", marginTop: 12 }}>
      <small>历史归档 · {a.archive_id}</small>
      {a.verified ? <>
        <p>第 {a.source_round} 轮 · 当时覆盖 {a.report.coverage.state} · 观察 {a.report.observed_at}</p>
        <p>不代表当前有效覆盖；不能证明停止、验收、交接处理、交付或 Goal 达成。</p>
        {a.retirement?.state === "retired" ? <>
          <p>扫描身份已退役 · 记录 {a.retirement.retired_at} · 固定快照 {a.retirement.coverage.snapshot_id}。退役凭据只固定旧身份；完整覆盖链仍需核验原归档。</p>
          <div className="task-actions">
            <button disabled={!client || busy} onClick={() => verifyRetirement(a)}>核验扫描退役身份</button>
            <button disabled={!client || busy} onClick={() => verifyRetirement(a, true)}>导出退役 JSON</button>
          </div>
        </> : a.retirement?.state === "unknown" ? <p>扫描退役凭据损坏或不可读 · unknown；不能确认退役记录。</p> : <>
          <p>独立退役身份尚未保存。旧归档仍保护原请求；释放时保存，已释放的旧版本可显式补记。</p>
          <button disabled={!client || busy || !records || records.active.some(row => row.report.scan_id === a.scan_id)} onClick={() => release(a)}>固定旧扫描退役身份</button>
        </>}
        <div className="task-actions">
          <button disabled={!client || busy} onClick={() => verify(a)}>核验历史归档</button>
          <button disabled={!client || busy} onClick={() => verify(a, true)}>导出历史 JSON</button>
        </div>
        <TaskCoverageFileCheck archive={a} session={session} client={client} busy={busy} run={run} />
      </> : <p>历史资料损坏或不可读 · unknown；不能用于释放活动容量。</p>}
    </div>)}
    {records && <div className="task-actions">
      {records.offset > 0 && <button disabled={!client || busy} onClick={() => load(records.offset - 16, records.retirement_offset)}>上一页历史资料</button>}
      {records.next_offset !== null && <button disabled={!client || busy} onClick={() => load(records.next_offset!, records.retirement_offset)}>下一页历史资料</button>}
    </div>}
    {records && <details className="task-coverage-retirements">
      <summary>独立退役身份资料（{records.retirement_count}）</summary>
      <p>原归档缺失或迁移后，退役记录仍固定旧请求。此列表不证明归档已安全迁移，不释放历史容量。</p>
      {records.retirements.map(row => <div key={row.scan_id} style={{ overflowWrap: "anywhere", marginTop: 12 }}>
        <small>退役 · 第 {row.source_round} 轮 · {row.scan_id}</small>
        <p>记录 {row.retired_at} · 当时覆盖 {row.coverage.state} · 快照 {row.coverage.snapshot_id}</p>
        <div className="task-actions">
          <button disabled={!client || busy} onClick={() => verifyRetirement(row)}>核验退役凭据 {row.scan_id}</button>
          <button disabled={!client || busy} onClick={() => verifyRetirement(row, true)}>导出退役凭据 {row.scan_id}</button>
        </div>
      </div>)}
      <div className="task-actions">
        {records.retirement_offset > 0 && <button disabled={!client || busy} onClick={() => load(records.offset, records.retirement_offset - 16)}>上一页退役资料</button>}
        {records.next_retirement_offset !== null && <button disabled={!client || busy} onClick={() => load(records.offset, records.next_retirement_offset!)}>下一页退役资料</button>}
      </div>
    </details>}
    {message && <p role="status">{message}</p>}
  </details>;
}
