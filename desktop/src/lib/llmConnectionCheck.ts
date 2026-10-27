import type { LlmConnectionTest } from "../types";
import { errorText } from "./errorText";

export type ConnectionDraft = { baseUrl: string; apiKey: string; model: string; providerId?: string };
export type ConnectionCheckState = { draft: ConnectionDraft; testing: boolean; result: LlmConnectionTest | null; error: string };
export const sameConnectionDraft = (a: ConnectionDraft, b: ConnectionDraft) =>
  a.baseUrl === b.baseUrl && a.apiKey === b.apiKey && a.model === b.model && a.providerId === b.providerId;

/** Only the current draft and attempt may receive model candidates or status. */
export function createConnectionCheck(
  currentDraft: () => ConnectionDraft,
  probe: (draft: ConnectionDraft) => Promise<LlmConnectionTest>,
  publish: (state: ConnectionCheckState) => void,
  models: (values: string[]) => void,
) {
  let generation = 0;
  return {
    reset() { generation += 1; },
    async test() {
      const attempt = ++generation;
      const draft = { ...currentDraft() };
      const current = () => attempt === generation && sameConnectionDraft(draft, currentDraft());
      publish({ draft, testing: true, result: null, error: "" });
      try {
        const result = await probe(draft);
        if (!current()) return;
        publish({ draft, testing: false, result, error: "" });
        if (result.models.length) models(result.models);
      } catch (failure) {
        if (current()) publish({ draft, testing: false, result: null, error: errorText(failure, "测试连接失败") });
      }
    },
  };
}
