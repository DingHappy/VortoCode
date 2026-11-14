import { useEffect, useLayoutEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { createAttachmentDraft, type AttachmentReader } from "../lib/attachmentDraft";

/** Attachments stay in memory and belong to one host/project/session, including pending reads. */
export function useAttachments(onBanner: (text: string) => void, owner: string, read?: AttachmentReader,
  retained?: ReturnType<typeof createAttachmentDraft>) {
  const draft = useMemo(() => retained ?? createAttachmentDraft(read), [owner, read, retained]);
  const current = useRef(draft); current.current = draft;
  const banner = useRef(onBanner); banner.current = onBanner;
  const view = useSyncExternalStore(draft.subscribe, draft.getSnapshot, draft.getSnapshot);
  const epoch = draft.getEpoch();
  useLayoutEffect(() => () => { if (retained) draft.suspend(); else draft.reset(); }, [draft, retained]);
  useEffect(() => { if (view.notice) banner.current(view.notice.text); }, [view.notice]);
  return { attachments: view.items, attachmentsReading: view.pending,
    addFiles: draft.addFiles, addAttachment: draft.addPrepared, removeAttachment: draft.remove,
    clearAttachments: draft.clear, takeAttachments: draft.take,
    cancelAttachmentReads: draft.cancelPending,
    resetAttachments: draft.reset,
    attachmentRevision: draft.getRevision,
    markComposerEdited: draft.edited,
    isCurrentAttachments: () => current.current === draft && draft.getEpoch() === epoch,
    isReadingAttachments: () => draft.getSnapshot().pending > 0 };
}
