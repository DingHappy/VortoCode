import { describe, expect, it } from "vitest";

import type { DesktopLlmProfileStatus } from "../types";
import { AUTO_MODEL, modelChoices, resolveModelChoice } from "./modelChoice";

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
