import { checkAttachment, classifyAttachment, MAX_DATA_URL_CHARS, type Attachment, type AttachmentKind } from "./attachments";

export interface AttachmentRead { result: Promise<string>; cancel: () => void }
export type AttachmentReader = (file: File, kind: AttachmentKind) => AttachmentRead;
export interface AttachmentDraftView { items: Attachment[]; pending: number; notice: { id: number; text: string } | null }

/** FileReader is local only. Cancelling a draft never starts another read. */
export const readAttachment: AttachmentReader = (file, kind) => {
  const reader = new FileReader();
  const result = new Promise<string>((resolve, reject) => {
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.onabort = () => reject(new Error("cancelled"));
    if (kind === "text") reader.readAsText(file);
    else reader.readAsDataURL(file);
  });
  return { result, cancel: () => { if (reader.readyState === FileReader.LOADING) reader.abort(); } };
};

let sequence = 0;
/** One in-memory draft per composer owner. Reserve quotas before any asynchronous read. */
export function createAttachmentDraft(read: AttachmentReader = readAttachment) {
  type Pending = { file: File; item: Attachment; done: () => void; cancel?: () => void };
  let view: AttachmentDraftView = { items: [], pending: 0, notice: null };
  let queue: Pending[] = [], running: Pending | null = null, revision = 0, noticeId = 0, epoch = 0;
  const listeners = new Set<() => void>();
  const publish = () => { view = { ...view, pending: queue.length }; for (const listener of listeners) listener(); };
  const notify = (text: string) => { view = { ...view, notice: { id: ++noticeId, text } }; publish(); };
  const reserved = () => [...view.items, ...queue.map(entry => entry.item)];
  const pump = () => {
    if (running || !queue.length) return;
    const entry = queue[0]; running = entry;
    const complete = (data?: string) => {
      // An aborted reader may still resolve. It cannot write into a cleared or replaced draft.
      if (running !== entry) return;
      running = null; queue = queue.filter(candidate => candidate !== entry);
      if (data !== undefined) { view = { ...view, items: [...view.items, { ...entry.item, data }] }; revision++; }
      entry.done(); publish(); pump();
    };
    try {
      const operation = read(entry.file, entry.item.kind); entry.cancel = operation.cancel;
      void operation.result.then(data => {
        if (running !== entry) return;
        if (entry.item.kind !== "text" && (!data.startsWith(`data:${entry.item.kind}/`) || data.length > MAX_DATA_URL_CHARS)) {
          notify(`${entry.item.name} 的文件类型或大小无法识别`); complete();
        } else complete(data);
      }, () => { if (running === entry) { notify(`读取 ${entry.item.name} 失败，请重新添加`); complete(); } });
    } catch { notify(`读取 ${entry.item.name} 失败，请重新添加`); complete(); }
  };
  const cancelPending = () => {
    const previous = queue, cancel = running?.cancel;
    running = null; queue = []; revision++; view = { ...view, pending: 0, notice: null };
    for (const entry of previous) entry.done();
    cancel?.(); publish();
  };
  const clear = () => { view = { ...view, items: [] }; cancelPending(); };
  return {
    getSnapshot: () => view,
    getEpoch: () => epoch,
    getRevision: () => revision,
    edited() { revision++; },
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    async addFiles(files: FileList | File[]) {
      const waits: Promise<void>[] = [];
      for (const file of Array.from(files)) {
        const name = file.name || (file.type.startsWith("image/") ? "粘贴的图片.png" : "附件");
        const kind = classifyAttachment(name, file.type);
        if (!kind) { notify(`暂不支持 ${name}：可以添加图片、音频或文本类文件`); continue; }
        const item = { id: `att-${++sequence}`, name, kind, size: file.size, data: "" };
        const check = checkAttachment(item, reserved());
        if (!check.ok) { notify(check.reason); continue; }
        waits.push(new Promise<void>(done => { queue.push({ file, item, done }); }));
        revision++;
      }
      publish(); pump(); await Promise.all(waits);
    },
    addPrepared(item: Attachment) {
      const check = checkAttachment(item, reserved().filter(candidate => candidate.id !== item.id));
      if (!check.ok) { notify(check.reason); return false; }
      view = { ...view, items: [...view.items.filter(candidate => candidate.id !== item.id), item] }; revision++; publish(); return true;
    },
    remove(id: string) { view = { ...view, items: view.items.filter(item => item.id !== id) }; revision++; publish(); },
    clear,
    cancelPending,
    /** Keep completed files when leaving a draft, but invalidate reads and send receipts. */
    suspend() { epoch++; cancelPending(); },
    reset() { epoch++; clear(); },
    /** Consume exactly the selection the user reviewed. A newer draft is never overwritten on failure. */
    take(expected: Attachment[], expectedRevision = revision) {
      if (queue.length || view.items !== expected || revision !== expectedRevision) return null;
      const items = view.items;
      view = { ...view, items: [] }; const receipt = ++revision; publish();
      return { items, restore() {
        if (receipt !== revision || view.items.length || queue.length) return false;
        view = { ...view, items }; revision++; publish(); return true;
      } };
    },
  };
}
