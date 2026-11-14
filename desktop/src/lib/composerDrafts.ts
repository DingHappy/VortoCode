import { createAttachmentDraft, type AttachmentReader } from "./attachmentDraft";

export interface ComposerOwner { draftKey: string; targetKey: string; sid: string }

/** Window-local unsent drafts. Existing session IDs are references, not a new session ledger. */
export function createComposerDrafts(read?: AttachmentReader) {
  const listeners = new Set<() => void>();
  let revision = 0;
  const publish = () => { revision++; for (const listener of listeners) listener(); };
  const entries = new Map<string, { owner: ComposerOwner; getText: () => string; setText: (value: string) => void;
    files: ReturnType<typeof createAttachmentDraft> }>();
  return {
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    getSnapshot: () => revision,
    get(owner: ComposerOwner) {
      let entry = entries.get(owner.draftKey);
      if (!entry) {
        let text = "";
        const files = createAttachmentDraft(read);
        files.subscribe(publish);
        entry = { owner, files, getText: () => text, setText(value) { if (text !== value) { text = value; publish(); } } };
        entries.set(owner.draftKey, entry);
      }
      return entry;
    },
    chats(targetKey: string) {
      return [...entries.values()].filter(entry => entry.owner.targetKey === targetKey
        && (entry.getText().trim() || entry.files.getSnapshot().items.length))
        .map(entry => ({ sid: entry.owner.sid, title: entry.getText().trim().split("\n")[0].slice(0, 60) || "未发送的附件" }));
    },
  };
}
