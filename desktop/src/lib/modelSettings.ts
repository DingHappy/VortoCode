import { isLocalModelBase, sameLlmBase } from "./providers";
import type { ProviderPreset } from "./providers";
import type { DesktopLlmProfileStatus } from "../types";
import { availableModels } from "./modelChoice";

export function roleModelOptions(profile: DesktopLlmProfileStatus | null) {
  if (!profile) return [];
  const defaults = [...new Set([profile.model, profile.fastModel, profile.strongModel, ...availableModels(profile).map(item => item.value)].filter((value): value is string => Boolean(value)))];
  return [...defaults.map(model => ({ value: `default:${model}`, label: `默认服务 · ${model}` })),
    ...(profile.providers ?? []).filter(provider => provider.hasKey || isLocalModelBase(provider.baseUrl))
      .flatMap(provider => provider.models.map(model => ({ value: `${provider.id}:${model}`, label: `${provider.name} · ${model}` })))];
}

export type ModelDraft = { baseUrl: string; model: string; apiKey: string; fastModel: string; strongModel: string; roleModels?: Record<string, string> };

export function normalizedRoleModels(roles: Record<string, string> = {}) {
  return Object.fromEntries(Object.entries(roles).map(([key, value]) => [key, value.trim()]).filter(([, value]) => value).sort(([a], [b]) => a.localeCompare(b)));
}

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
  const rolesDirty = JSON.stringify(normalizedRoleModels(profile?.roleModels)) !== JSON.stringify(normalizedRoleModels(draft.roleModels));
  const missing = !draft.baseUrl.trim() ? "请填写模型服务地址。"
    : !draft.model.trim() ? "请填写模型名，或先获取模型列表。"
    : !isLocalModelBase(draft.baseUrl) && !draft.apiKey.trim() && !sameService ? "新云端服务需要填写 API Key。" : "";
  return { sameService, sameModel, dirty: dirty || rolesDirty, missing };
}
