export interface MemorySnapshot {
  version: 1;
  revision: string;
  exported_at: string;
  scope: string;
  instance_id: string;
  total: number;
  count: number;
  limit: number;
  coverage_limited: boolean;
  items: Array<{ id: string; content: string; recorded_at: string; source: string; confirmed_by: string; tainted: boolean; redacted: boolean }>;
  markdown: string;
}

/** An export is background data; never feed it back as an approval or import. */
export function parseMemorySnapshot(value: unknown): MemorySnapshot {
  const v = value as MemorySnapshot | null;
  const text = (s: unknown, limit: number) => typeof s === "string" && s.length <= limit;
  if (!v || v.version !== 1 || !text(v.revision, 64) || !/^[a-f0-9]{64}$/.test(v.revision)
    || !text(v.exported_at, 80) || !text(v.scope, 160) || !text(v.instance_id, 32) || !text(v.markdown, 8_000_000)
    || !Number.isSafeInteger(v.total) || v.total < 0 || !Number.isSafeInteger(v.count) || v.count < 0
    || !Number.isSafeInteger(v.limit) || v.limit < 1 || v.limit > 1000 || v.count > v.limit || v.count > v.total
    || typeof v.coverage_limited !== "boolean" || v.coverage_limited !== (v.count < v.total)
    || !Array.isArray(v.items) || v.items.length !== v.count
    || v.items.some(i => !i || !text(i.id, 160) || !text(i.content, 4000) || !text(i.recorded_at, 80)
      || !text(i.source, 160) || !text(i.confirmed_by, 160) || typeof i.tainted !== "boolean" || typeof i.redacted !== "boolean")) {
    throw new Error("记忆快照格式不完整，请重新读取");
  }
  return v;
}

export function downloadMemorySnapshot(snapshot: MemorySnapshot) {
  const url = URL.createObjectURL(new Blob([snapshot.markdown], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `MEMORY-${snapshot.revision.slice(0, 8)}.md`;
  document.body.appendChild(anchor);
  try { anchor.click(); } finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
}
