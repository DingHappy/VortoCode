import { expect, it, vi } from "vitest";
import { createComposerDrafts } from "./composerDrafts";

const a = { draftKey: "a/chat", targetKey: "a", sid: "chat" };
const b = { draftKey: "b/chat", targetKey: "b", sid: "chat" };
it("keeps text and completed files in their space and session, including unsent side chats", () => {
  const drafts = createComposerDrafts();
  const first = drafts.get(a);
  first.setText("Only for A");
  first.files.addPrepared({ id: "one", name: "a.md", kind: "text", size: 1, data: "a" });
  first.files.suspend();
  const second = drafts.get(b);
  expect(second.getText()).toBe(""); expect(second.files.getSnapshot().items).toEqual([]);
  second.setText("Only for B");
  expect(drafts.get(a).getText()).toBe("Only for A");
  expect(drafts.get(a).files.getSnapshot().items[0].name).toBe("a.md");
  expect(drafts.chats("a")).toEqual([{ sid: "chat", title: "Only for A" }]);
  first.setText(""); first.files.clear();
  expect(drafts.chats("a")).toEqual([]);
  expect(second.getText()).toBe("Only for B");
});

it("invalidates late file reads and send recovery after leaving and returning to A", async () => {
  let resolve!: (data: string) => void;
  const cancel = vi.fn();
  const drafts = createComposerDrafts(() => ({ result: new Promise<string>(yes => { resolve = yes; }), cancel }));
  const first = drafts.get(a);
  first.files.addPrepared({ id: "one", name: "a.md", kind: "text", size: 1, data: "a" });
  const receipt = first.files.take(first.files.getSnapshot().items)!;
  const epoch = first.files.getEpoch();
  const pending = first.files.addFiles([{ name: "late.md", size: 1, type: "" } as File]);
  first.files.suspend();
  expect(cancel).toHaveBeenCalledOnce();
  drafts.get(b).setText("B");
  const returned = drafts.get(a);
  resolve("late content"); await pending; await Promise.resolve();
  expect(returned.files.getEpoch()).not.toBe(epoch);
  expect(returned.files.getSnapshot().items).toEqual([]);
  expect(receipt.restore()).toBe(false);
  expect(drafts.get(b).getText()).toBe("B");
});
