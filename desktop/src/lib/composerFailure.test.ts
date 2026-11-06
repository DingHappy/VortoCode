import { expect, it, vi } from "vitest";
import { reportComposerFailure } from "./composerFailure";
import { createAttachmentDraft } from "./attachmentDraft";

it("switching sessions still reports failed delivery without restoring the old input", () => {
  for (const message of ["这条消息没确认送达", "发送失败"]) {
    const restore = vi.fn(() => true), recover = vi.fn(), markFailed = vi.fn(), notify = vi.fn();
    expect(reportComposerFailure({ current: false, restore, recover, markFailed, notify, message })).toBe(false);
    expect(markFailed).toHaveBeenCalledOnce(); expect(notify).toHaveBeenCalledWith(`${message}；已切换会话，原输入未恢复`);
    expect(restore).not.toHaveBeenCalled(); expect(recover).not.toHaveBeenCalled();
  }
});

it("recovers the original selection once and reports failure while preserving a newer draft", () => {
  const draft = createAttachmentDraft();
  draft.addPrepared({ id: "one", name: "one.md", size: 1, kind: "text", data: "original" });
  const receipt = draft.take(draft.getSnapshot().items)!;
  const recover = vi.fn(), notify = vi.fn(), markFailed = vi.fn();
  expect(reportComposerFailure({ current: true, restore: receipt.restore, recover, notify, markFailed, message: "发送失败" })).toBe(true);
  expect(recover).toHaveBeenCalledOnce(); expect(draft.getSnapshot().items[0].data).toBe("original");
  const next = draft.take(draft.getSnapshot().items)!; draft.edited();
  expect(reportComposerFailure({ current: true, restore: next.restore, recover, notify, markFailed, message: "没确认送达" })).toBe(false);
  expect(recover).toHaveBeenCalledOnce(); expect(markFailed).toHaveBeenCalledTimes(2);
  expect(notify).toHaveBeenLastCalledWith("没确认送达；当前草稿已保留");
});
