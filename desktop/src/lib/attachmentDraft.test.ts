import { expect, it, vi } from "vitest";
import { createAttachmentDraft, type AttachmentReader } from "./attachmentDraft";
import { MAX_DATA_URL_CHARS, type Attachment } from "./attachments";

const file = (name = "notes.md", size = 10) => ({ name, size, type: "" }) as File;
const prepared = (name = "ready.md"): Attachment => ({ id: name, name, kind: "text", size: 10, data: "saved" });
function controlled() {
  const operations: { name: string; resolve: (data: string) => void; reject: () => void; cancel: ReturnType<typeof vi.fn> }[] = [];
  const read: AttachmentReader = input => {
    let resolve!: (data: string) => void, reject!: () => void;
    const result = new Promise<string>((yes, no) => { resolve = yes; reject = () => no(new Error("synthetic read failure")); });
    const cancel = vi.fn(); operations.push({ name: input.name, resolve, reject, cancel });
    return { result, cancel };
  };
  return { draft: createAttachmentDraft(read), operations };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

it("reserves media and byte quotas across overlapping additions before reads resolve", async () => {
  const { draft, operations } = controlled();
  const first = draft.addFiles(Array.from({ length: 4 }, (_, index) => file(`${index}.png`)));
  const second = draft.addFiles(Array.from({ length: 3 }, (_, index) => file(`more-${index}.png`)));
  expect(draft.getSnapshot().pending).toBe(6); expect(draft.getSnapshot().notice?.text).toContain("最多 6");
  expect(operations).toHaveLength(1); // Read sequentially, keeping deterministic input order.
  expect(draft.take(draft.getSnapshot().items)).toBeNull();
  for (let index = 0; index < 6; index++) { operations[index].resolve("data:image/png;base64,AA"); await flush(); }
  await Promise.all([first, second]);
  expect(draft.getSnapshot().items.map(item => item.name)).toEqual(["0.png", "1.png", "2.png", "3.png", "more-0.png", "more-1.png"]);
  expect(draft.getSnapshot().pending).toBe(0);

  const other = controlled();
  const a = other.draft.addFiles([file("a.md", 100 * 1024), file("b.md", 100 * 1024)]);
  const b = other.draft.addFiles([file("c.md", 1)]);
  expect(other.draft.getSnapshot().pending).toBe(2); expect(other.draft.getSnapshot().notice?.text).toContain("200KB");
  other.draft.clear(); await Promise.all([a, b]);
});

it("clears pending work, aborts the active read and ignores late results and errors", async () => {
  const { draft, operations } = controlled();
  const old = draft.addFiles([file("old.md"), file("not-started.md")]);
  const epoch = draft.getEpoch(); draft.reset();
  expect(draft.getEpoch()).not.toBe(epoch); expect(operations[0].cancel).toHaveBeenCalledOnce();
  expect(operations).toHaveLength(1); expect(draft.getSnapshot()).toMatchObject({ items: [], pending: 0, notice: null });
  const current = draft.addFiles([file("new.md")]);
  operations[0].resolve("old content"); await flush();
  expect(draft.getSnapshot().items).toEqual([]); expect(draft.getSnapshot().pending).toBe(1);
  operations[1].resolve("new content"); await Promise.all([old, current]);
  expect(draft.getSnapshot().items.map(item => item.data)).toEqual(["new content"]);
});

it("releases failed reservations and continues without silently adding unread files", async () => {
  const { draft, operations } = controlled();
  const added = draft.addFiles([file("bad.md"), file("good.md")]);
  operations[0].reject(); await flush();
  expect(draft.getSnapshot().notice?.text).toContain("bad.md 失败");
  operations[1].resolve("good content"); await added;
  expect(draft.getSnapshot().items.map(item => item.name)).toEqual(["good.md"]);
  expect(draft.getSnapshot().pending).toBe(0);
});

it("rejects unsupported and oversized files before reading, and verifies media after reading", async () => {
  const { draft, operations } = controlled();
  await draft.addFiles([file("book.pdf"), file("huge.md", 101 * 1024)]);
  expect(operations).toEqual([]);
  const wrong = draft.addFiles([file("wrong.png")]);
  operations[0].resolve("data:audio/mpeg;base64,AA"); await wrong;
  const large = draft.addFiles([file("large.png")]);
  operations[1].resolve("data:image/png;base64," + "A".repeat(MAX_DATA_URL_CHARS)); await large;
  expect(draft.getSnapshot().items).toEqual([]); expect(draft.getSnapshot().pending).toBe(0);
});

it("restores a failed send once, but never replaces newer selections or edited text", () => {
  const { draft } = controlled(); draft.addPrepared(prepared());
  const selection = draft.getSnapshot().items, receipt = draft.take(selection)!;
  expect(draft.getSnapshot().items).toEqual([]); expect(draft.take(selection)).toBeNull();
  expect(receipt.restore()).toBe(true); expect(receipt.restore()).toBe(false);
  const replaced = draft.take(draft.getSnapshot().items)!;
  draft.addPrepared(prepared("new.md")); expect(replaced.restore()).toBe(false);
  expect(draft.getSnapshot().items[0].name).toBe("new.md");
  const textEdited = draft.take(draft.getSnapshot().items)!;
  draft.edited(); expect(textEdited.restore()).toBe(false);
  const revision = draft.getRevision(), items = draft.getSnapshot().items;
  draft.edited(); expect(draft.take(items, revision)).toBeNull();
  const reset = draft.take(draft.getSnapshot().items)!;
  const epoch = draft.getEpoch();
  draft.clear(); expect(reset.restore()).toBe(false); expect(draft.getEpoch()).toBe(epoch);
});

it("counts prepared offline-chat attachments against pending text quota", async () => {
  const { draft } = controlled(); const loading = draft.addFiles([file("pending.md", 100 * 1024)]);
  expect(draft.addPrepared({ ...prepared(), size: 100 * 1024 })).toBe(true);
  expect(draft.addPrepared(prepared("too-many.md"))).toBe(false);
  draft.clear(); await loading;
});

it("cancels only unfinished reads and keeps the two attachments already ready", async () => {
  const { draft, operations } = controlled();
  const added = draft.addFiles([file("one.md"), file("two.md"), file("three.md"), file("queued.md")]);
  operations[0].resolve("one"); await flush(); operations[1].resolve("two"); await flush();
  expect(draft.getSnapshot().items.map(item => item.name)).toEqual(["one.md", "two.md"]);
  draft.cancelPending(); await added;
  expect(operations[2].cancel).toHaveBeenCalledOnce(); expect(operations).toHaveLength(3);
  expect(draft.getSnapshot().pending).toBe(0);
  operations[2].resolve("late"); await flush();
  expect(draft.getSnapshot().items.map(item => item.name)).toEqual(["one.md", "two.md"]);
  const next = draft.addFiles([file("new.md")]); operations[3].resolve("new"); await next;
  expect(draft.getSnapshot().items.map(item => item.name)).toEqual(["one.md", "two.md", "new.md"]);
});
