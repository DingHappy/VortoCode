// 输入框的模型选择：「自动」按任务调度，或固定用某个模型。
// 输入框只列少量常用模型：主模型 + 快速 / 强力档，加上一份短名单（默认取服务端推荐，
// 可在「设置 → 模型」里增删、调整顺序）；服务上的其余模型都在设置里挑。
import type { DesktopLlmProfileStatus, ModelInfo } from "../types";
import { STORAGE_KEYS } from "./storage";

export const AUTO_MODEL = "auto";

export interface ModelChoice {
  value: string;
  label: string;
  hint: string;
  /** 自定义供应商的名称；默认服务的模型没有分组。 */
  group?: string;
  /** 能力与上下文长度的短标签，例如「看图」「推理」「1M」。 */
  badges?: string[];
}

export const ALL_MODELS_GROUP = "全部模型";
/** 输入框里的短名单：没调整过时是服务端推荐，调整过就是用户自己的常用模型。 */
export const RECOMMENDED_GROUP = "推荐";
export const PICKED_GROUP = "常用";
export const MODEL_GROUPS = {
  coding: "推荐 · 编程",
  fast: "推荐 · 快速",
  omni: "全模态",
  multimodal: "多模态",
  chat: "对话",
  snapshots: "更多版本",
} as const;
const CHAT_CATEGORIES = new Set(["chat", "multimodal", "omni"]);
const CAPABILITY_BADGES: Array<[string, string]> = [["vision", "看图"], ["audio_input", "听音频"], ["reasoning", "推理"]];

const NON_CHAT_MARKERS = ["tts", "asr", "whisper", "embedding", "embed", "rerank", "dall-e", "image", "voice", "audio", "moderation"];

/** 粗略判断一个模型能不能拿来对话：名字里带语音/向量/生图标记的不列进选择器。 */
export function isChatModel(model: string): boolean {
  const name = model.toLowerCase();
  return !NON_CHAT_MARKERS.some((marker) => name.includes(marker));
}

/** 能跑 Agent：服务端标为能对话的类别，且支持工具调用。 */
export function isAgentReady(info: ModelInfo): boolean {
  return !!info.category && CHAT_CATEGORIES.has(info.category) && (info.capabilities ?? []).includes("tools");
}

/** 上下文长度的简写：按 1000 整除的用十进制（128000 → 128K），否则用二进制单位（1048576 → 1M）。 */
export function formatContextWindow(tokens: number): string {
  const base = tokens % 1000 === 0 ? 1000 : 1024;
  const scaled = tokens >= base * base ? [tokens / (base * base), "M"] as const : [tokens / base, "K"] as const;
  return `${Number(scaled[0].toFixed(1))}${scaled[1]}`;
}

export function modelBadges(info: ModelInfo | undefined): string[] {
  if (!info) return [];
  const capabilities = info.capabilities ?? [];
  const badges = CAPABILITY_BADGES.filter(([capability]) => capabilities.includes(capability)).map(([, label]) => label);
  if (info.contextWindow) badges.push(formatContextWindow(info.contextWindow));
  return badges;
}

/** 服务端分好类的模型怎么归组：快照版收在最后，其余按推荐档位、再按类别。 */
function serviceGroup(info: ModelInfo): string {
  if (info.snapshotOf) return MODEL_GROUPS.snapshots;
  if (info.tier === "coding") return MODEL_GROUPS.coding;
  if (info.tier === "fast") return MODEL_GROUPS.fast;
  if (info.category === "omni") return MODEL_GROUPS.omni;
  if (info.category === "multimodal") return MODEL_GROUPS.multimodal;
  return MODEL_GROUPS.chat;
}

const GROUP_ORDER: string[] = [MODEL_GROUPS.coding, MODEL_GROUPS.fast, MODEL_GROUPS.omni, MODEL_GROUPS.multimodal, MODEL_GROUPS.chat, MODEL_GROUPS.snapshots];

/** 已配置的几档（主模型 / 快速 / 强力），它们总在输入框里。 */
function configuredModels(profile: DesktopLlmProfileStatus): Set<string> {
  return new Set([profile.fastModel, profile.model, profile.strongModel].map((model) => model?.trim() ?? "").filter(Boolean));
}

/** 默认服务上的其他模型（设置里可挑的全集）。服务给了分类就按分类分组，只列能跑 Agent 的；没给分类（别家服务）按名字粗筛。 */
export function availableModels(profile: DesktopLlmProfileStatus | null): ModelChoice[] {
  return profile?.model ? serviceModelChoices(profile, configuredModels(profile)) : [];
}

/** 服务端推荐（编程主力在前、快速在后）；别家服务没有推荐，短名单默认为空。 */
export function recommendedModels(profile: DesktopLlmProfileStatus | null): string[] {
  return availableModels(profile)
    .filter((choice) => choice.group === MODEL_GROUPS.coding || choice.group === MODEL_GROUPS.fast)
    .map((choice) => choice.value);
}

function serviceModelChoices(profile: DesktopLlmProfileStatus, configured: Set<string>): ModelChoice[] {
  const infos = profile.modelInfo ?? [];
  if (infos.some((info) => info.category)) {
    return infos
      .filter((info) => isAgentReady(info) && !configured.has(info.id))
      .map((info) => {
        const group = serviceGroup(info);
        const badges = modelBadges(info);
        return { value: info.id, label: info.id, hint: [group, ...badges].join(" · "), group, badges };
      })
      // 稳定排序：组内保持服务端返回的顺序。
      .sort((left, right) => GROUP_ORDER.indexOf(left.group) - GROUP_ORDER.indexOf(right.group));
  }
  return (profile.models ?? [])
    .filter((model) => !configured.has(model) && isChatModel(model))
    .map((model) => ({ value: model, label: model, hint: ALL_MODELS_GROUP, group: ALL_MODELS_GROUP }));
}

/** picks 为 null 表示没调整过，用服务端推荐；名单里已不在服务上的模型自动略过。 */
export function modelChoices(profile: DesktopLlmProfileStatus | null, picks: string[] | null = null): ModelChoice[] {
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
  const info = new Map((profile.modelInfo ?? []).map((item) => [item.id, item]));
  for (const choice of own) {
    const badges = modelBadges(info.get(choice.value));
    if (badges.length) choice.badges = badges;
  }
  const available = new Map(serviceModelChoices(profile, new Set(seen.keys())).map((choice) => [choice.value, choice]));
  const group = picks ? PICKED_GROUP : RECOMMENDED_GROUP;
  const listed = [...new Set(picks ?? recommendedModels(profile))].flatMap((model) => {
    const choice = available.get(model);
    return choice ? [{ ...choice, group }] : [];
  });
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

/** 输入框短名单；没存过（或存的不是字符串数组）返回 null，表示跟随服务端推荐。 */
export function loadModelPicks(): string[] | null {
  try {
    const payload: unknown = JSON.parse(localStorage.getItem(STORAGE_KEYS.modelPicks) ?? "null");
    return Array.isArray(payload) && payload.every((item) => typeof item === "string") ? payload : null;
  } catch {
    return null;
  }
}

export function persistModelPicks(picks: string[] | null): void {
  try {
    if (picks) localStorage.setItem(STORAGE_KEYS.modelPicks, JSON.stringify(picks));
    else localStorage.removeItem(STORAGE_KEYS.modelPicks);
  } catch {
    // 存不下只是下次回到推荐名单。
  }
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
