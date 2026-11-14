import { useCallback, useRef, useSyncExternalStore, type SetStateAction } from "react";
import { createComposerDrafts, type ComposerOwner } from "../lib/composerDrafts";
import { useAttachments } from "./useAttachments";

export function useComposerDraft(owner: ComposerOwner, onBanner: (text: string) => void) {
  const pool = useRef<ReturnType<typeof createComposerDrafts> | null>(null);
  if (!pool.current) pool.current = createComposerDrafts();
  useSyncExternalStore(pool.current.subscribe, pool.current.getSnapshot, pool.current.getSnapshot);
  const draft = pool.current.get(owner);
  const files = useAttachments(onBanner, owner.draftKey, undefined, draft.files);
  const setPromptState = useCallback((value: SetStateAction<string>) => {
    draft.setText(typeof value === "function" ? value(draft.getText()) : value);
  }, [draft]);
  return { ...files, prompt: draft.getText(), setPromptState, draftChats: pool.current.chats(owner.targetKey) };
}
