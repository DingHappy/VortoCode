import { describe, expect, it } from "vitest";

import type { DesktopLlmProfileStatus } from "../types";
import {
  ALL_MODELS_GROUP, AUTO_MODEL, availableModels, formatContextWindow, MODEL_GROUPS, modelChoices, PICKED_GROUP, RECOMMENDED_GROUP,
  recommendedModels, resolveModelChoice,
} from "./modelChoice";

const profile = (extra: Partial<DesktopLlmProfileStatus>): DesktopLlmProfileStatus => ({
  configured: true, baseUrl: "https://x.example/v1", model: "main", provider: "custom", requiresKey: true, ...extra,
});

describe("modelChoices", () => {
  it("offers auto plus each distinct configured model", () => {
    expect(modelChoices(profile({ fastModel: "fast", strongModel: "strong" })).map((choice) => choice.value))
      .toEqual([AUTO_MODEL, "fast", "main", "strong"]);
  });

  it("merges duplicates and drops auto when only one model exists", () => {
    const choices = modelChoices(profile({ fastModel: "main", strongModel: " " }));
    expect(choices).toEqual([{ value: "main", label: "main", hint: "快速 · 主模型" }]);
    expect(modelChoices(null)).toEqual([]);
  });
});

describe("default service model list", () => {
  it("offers the service's chat models in settings under 全部模型, skipping speech/embedding models", () => {
    const withModels = profile({ models: ["main", "fast-chat", "mimo-v2.5-tts", "text-embedding-3", "whisper-1"] });
    expect(availableModels(withModels).map((choice) => [choice.value, choice.group])).toEqual([["fast-chat", ALL_MODELS_GROUP]]);
    // 别家服务没有推荐：输入框默认只列已配置的模型，其余要在设置里加。
    expect(modelChoices(withModels).map((choice) => choice.value)).toEqual(["main"]);
    expect(modelChoices(withModels, ["fast-chat"]).map((choice) => choice.value)).toEqual(["main", "fast-chat"]);
  });
});

describe("default service model list with relay categories", () => {
  const tools = ["tools"];
  const modelInfo = [
    { id: "main", category: "chat", capabilities: tools, tier: "coding", contextWindow: 262144 },
    { id: "mimo-v2.6-pro", category: "omni", capabilities: ["vision", "audio_input", "tools", "reasoning"], tier: "coding", contextWindow: 1048576 },
    { id: "qwen3-coder-flash", category: "chat", capabilities: tools, tier: "fast" },
    { id: "qwen3.8-omni-flash", category: "omni", capabilities: ["audio_input", "tools"] },
    { id: "glm-vision", category: "multimodal", capabilities: ["vision", "tools"], contextWindow: 128000 },
    { id: "plain-chat", category: "chat", capabilities: tools },
    { id: "qwen3.7-max-2026-05-17", category: "chat", capabilities: tools, snapshotOf: "qwen3.7-max" },
    { id: "qwen-mt-plus", category: "translation", capabilities: tools },
    { id: "qwen-vl-ocr", category: "ocr", capabilities: ["vision", "tools"] },
    { id: "wan2.7-t2v", category: "video" },
    { id: "mimo-v2.5-tts", category: "tts" },
    { id: "chat-no-tools", category: "chat", capabilities: ["reasoning"] },
  ];
  const relay = profile({ models: modelInfo.map((info) => info.id), modelInfo });
  const choices = availableModels(relay);

  it("groups the full list by the service's tier and category, with snapshots last", () => {
    expect(choices.map((choice) => [choice.value, choice.group])).toEqual([
      ["mimo-v2.6-pro", MODEL_GROUPS.coding],
      ["qwen3-coder-flash", MODEL_GROUPS.fast],
      ["qwen3.8-omni-flash", MODEL_GROUPS.omni],
      ["glm-vision", MODEL_GROUPS.multimodal],
      ["plain-chat", MODEL_GROUPS.chat],
      ["qwen3.7-max-2026-05-17", MODEL_GROUPS.snapshots],
    ]);
  });

  it("leaves out special-purpose, non-chat and tool-less models", () => {
    const values = choices.map((choice) => choice.value);
    for (const excluded of ["qwen-mt-plus", "qwen-vl-ocr", "wan2.7-t2v", "mimo-v2.5-tts", "chat-no-tools"]) {
      expect(values).not.toContain(excluded);
    }
  });

  it("labels capabilities and context length, including on the configured model", () => {
    expect(choices.find((choice) => choice.value === "mimo-v2.6-pro")?.badges).toEqual(["看图", "听音频", "推理", "1M"]);
    expect(choices.find((choice) => choice.value === "glm-vision")?.badges).toEqual(["看图", "128K"]);
    expect(modelChoices(relay)[0]).toMatchObject({ value: "main", hint: "主模型", badges: ["256K"] });
  });

  it("keeps the composer short: configured models plus the service's recommendations by default", () => {
    expect(recommendedModels(relay)).toEqual(["mimo-v2.6-pro", "qwen3-coder-flash"]);
    expect(modelChoices(relay).map((choice) => [choice.value, choice.group])).toEqual([
      ["main", undefined],
      ["mimo-v2.6-pro", RECOMMENDED_GROUP],
      ["qwen3-coder-flash", RECOMMENDED_GROUP],
    ]);
  });

  it("uses the user's own ordered picks, dropping ones the service no longer offers or can't run", () => {
    const picks = ["glm-vision", "gone-model", "qwen-mt-plus", "mimo-v2.6-pro", "glm-vision", "main"];
    expect(modelChoices(relay, picks).map((choice) => [choice.value, choice.group])).toEqual([
      ["main", undefined],
      ["glm-vision", PICKED_GROUP],
      ["mimo-v2.6-pro", PICKED_GROUP],
    ]);
    expect(modelChoices(relay, []).map((choice) => choice.value)).toEqual(["main"]);
  });

  it("formats context windows in binary or decimal units", () => {
    expect([1048576, 1000000, 262144, 128000, 131072, 2097152].map(formatContextWindow))
      .toEqual(["1M", "1M", "256K", "128K", "128K", "2M"]);
  });
});

describe("default service model list without categories", () => {
  it("falls back to name filtering when modelInfo carries ids only", () => {
    const models = ["main", "gpt-4o", "whisper-1", "text-embedding-3"];
    const plain = profile({ models, modelInfo: models.map((id) => ({ id })) });
    expect(availableModels(plain).map((choice) => [choice.value, choice.group])).toEqual([["gpt-4o", ALL_MODELS_GROUP]]);
    expect(recommendedModels(plain)).toEqual([]);
  });
});

describe("custom providers", () => {
  it("lists their models as provider:model in their own group", () => {
    const choices = modelChoices(profile({
      providers: [{ id: "deepseek", name: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", models: ["deepseek-chat"], hasKey: true }],
    }));
    expect(choices.map((choice) => choice.value)).toEqual(["main", "deepseek:deepseek-chat"]);
    expect(choices[1]).toMatchObject({ label: "deepseek-chat", group: "DeepSeek" });
  });
});

describe("resolveModelChoice", () => {
  it("keeps a still-valid saved choice and falls back otherwise", () => {
    const choices = modelChoices(profile({ strongModel: "strong" }));
    expect(resolveModelChoice("strong", choices)).toBe("strong");
    expect(resolveModelChoice("removed-model", choices)).toBe(AUTO_MODEL);
    expect(resolveModelChoice(null, [])).toBe("");
  });
});
