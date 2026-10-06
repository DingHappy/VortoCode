// 输入框的模型选择：「自动」按任务调度，或固定用某个已配置的模型。
// 可选项只来自本机模型配置（主模型 + 快速 / 强力档），服务端也只接受这几个。
import type { DesktopLlmProfileStatus } from "../types";
import { STORAGE_KEYS } from "./storage";

export const AUTO_MODEL = "auto";

export interface ModelChoice {
  value: string;
  label: string;
  hint: string;
  /** 自定义供应商的名称；默认服务的模型没有分组。 */
  group?: string;
}

export const ALL_MODELS_GROUP = "全部模型";

const NON_CHAT_MARKERS = ["tts", "asr", "whisper", "embedding", "embed", "rerank", "dall-e", "image", "voice", "audio", "moderation"];

/** 粗略判断一个模型能不能拿来对话：名字里带语音/向量/生图标记的不列进选择器。 */
export function isChatModel(model: string): boolean {
  const name = model.toLowerCase();
  return !NON_CHAT_MARKERS.some((marker) => name.includes(marker));
}

export function modelChoices(profile: DesktopLlmProfileStatus | null): ModelChoice[] {
  if (!profile?.model) return [];
  const tiers: Array<[string | undefined, string]> = [
    [profile.fastModel, "快速"],
    [profile.model, "主模型"],
    [profile.strongModel, "强力"],
  ];
  const seen = new Map<string, ModelChoice>();
  for (const [model, hint] of tiers) {
    const name = model?.trim();
    if (!name) continue;
    const existing = seen.get(name);
    if (existing) existing.hint = `${existing.hint} · ${hint}`;
    else seen.set(name, { value: name, label: name, hint });
  }
  const own = [...seen.values()];
  // 默认服务上的其他模型：放在「全部模型」组里；语音合成/识别、向量、生图这类不能对话的不列。
  const listed = (profile.models ?? [])
    .filter((model) => !seen.has(model) && isChatModel(model))
    .map((model) => ({ value: model, label: model, hint: "全部模型", group: ALL_MODELS_GROUP }));
  // 自定义供应商的模型以 `ID:模型` 发给 runtime，由它换到对应端点和 Key。
  const custom = (profile.providers ?? []).flatMap((provider) => provider.hasKey || provider.models.length
    ? provider.models.map((model) => ({ value: `${provider.id}:${model}`, label: model, hint: provider.name, group: provider.name }))
    : []);
  // 「自动」只在默认服务的几档之间调度；只有一个默认模型时它没有可选的，不提供。
  const auto = own.length > 1 ? [{ value: AUTO_MODEL, label: "自动", hint: "按任务选择快速或强力模型" }] : [];
  return [...auto, ...own, ...listed, ...custom];
}

/** 已存的选择仍然有效就用它，否则回到「自动」（或唯一的模型）。 */
export function resolveModelChoice(saved: string | null, choices: ModelChoice[]): string {
  if (saved && choices.some((choice) => choice.value === saved)) return saved;
  return choices[0]?.value ?? "";
}

export function loadModelChoice(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEYS.modelChoice);
  } catch {
    return null;
  }
}

export function persistModelChoice(value: string): void {
  try {
    localStorage.setItem(STORAGE_KEYS.modelChoice, value);
  } catch {
    // 存不下只是下次回到默认，不影响本次选择。
  }
}
