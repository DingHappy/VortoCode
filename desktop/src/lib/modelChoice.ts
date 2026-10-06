// 输入框的模型选择：「自动」按任务调度，或固定用某个已配置的模型。
// 可选项只来自本机模型配置（主模型 + 快速 / 强力档），服务端也只接受这几个。
import type { DesktopLlmProfileStatus } from "../types";
import { STORAGE_KEYS } from "./storage";

export const AUTO_MODEL = "auto";

export interface ModelChoice {
  value: string;
  label: string;
  hint: string;
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
  const models = [...seen.values()];
  // 只有一个模型时「自动」没有可选的，不提供。
  return models.length > 1
    ? [{ value: AUTO_MODEL, label: "自动", hint: "按任务选择快速或强力模型" }, ...models]
    : models;
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
