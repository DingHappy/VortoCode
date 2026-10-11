import { isLocalModelBase, sameLlmBase } from "./providers";
import type { ProviderPreset } from "./providers";
import type { DesktopLlmProfileStatus } from "../types";

export type ModelDraft = { baseUrl: string; model: string; apiKey: string; fastModel: string; strongModel: string };

/** A preset is a convenience, not permission to discard a working local endpoint. */
export function applyModelPreset(draft: ModelDraft, preset: ProviderPreset): ModelDraft {
  if (sameLlmBase(draft.baseUrl, preset.baseUrl)
    || (!preset.requiresKey && isLocalModelBase(draft.baseUrl))) return draft;
  return { ...draft, baseUrl: preset.baseUrl, model: preset.model, apiKey: "" };
}

export function modelSettingsState(profile: DesktopLlmProfileStatus | null, draft: ModelDraft) {
  const sameService = Boolean(profile?.configured && sameLlmBase(profile.baseUrl, draft.baseUrl));
  const sameModel = sameService && profile?.model === draft.model.trim();
  const dirty = !sameModel || Boolean(draft.apiKey.trim())
    || (profile?.fastModel ?? "") !== draft.fastModel.trim()
    || (profile?.strongModel ?? "") !== draft.strongModel.trim();
  const missing = !draft.baseUrl.trim() ? "请填写模型服务地址。"
    : !draft.model.trim() ? "请填写模型名，或先获取模型列表。"
    : !isLocalModelBase(draft.baseUrl) && !draft.apiKey.trim() && !sameService ? "新云端服务需要填写 API Key。" : "";
  return { sameService, sameModel, dirty, missing };
}
