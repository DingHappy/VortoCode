import type { MemorySnapshot } from "../lib/memorySnapshot";
import { downloadMemorySnapshot } from "../lib/memorySnapshot";

export function MemorySnapshotDialog({ snapshot, busy, error, onRetry, onClose }: {
  snapshot: MemorySnapshot | null; busy: boolean; error: string; onRetry: () => void; onClose: () => void;
}) {
  return <div className="modal-backdrop"><section className="settings-modal memory-snapshot-dialog" role="dialog" aria-modal="true" aria-label="导出记忆">
    <h2>导出记忆</h2>
    <p>保存一份可阅读的 Markdown 快照。修改文件不会改变助理记忆。</p>
    {busy && <p role="status">正在读取记忆…</p>}
    {error && <p role="alert">{error}</p>}
    {snapshot && <>
      <p>已读取 {snapshot.count} / {snapshot.total} 条 · {snapshot.scope}</p>
      {snapshot.coverage_limited && <p role="status">部分记录未导出，请查看文件中的范围说明。</p>}
      <p>这份文件可能包含私人资料，请妥善保存。来源缺失时显示未知。</p>
      <label>文件预览<textarea readOnly rows={14} value={snapshot.markdown} /></label>
    </>}
    <div className="settings-actions">
      <button onClick={onClose}>关闭</button>
      {!busy && <button onClick={onRetry}>重新读取</button>}
      <button disabled={!snapshot || busy} onClick={() => snapshot && downloadMemorySnapshot(snapshot)}>下载 Markdown</button>
    </div>
  </section></div>;
}
