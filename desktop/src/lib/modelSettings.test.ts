import { expect, it } from "vitest";
import { applyModelPreset, modelSettingsState } from "./modelSettings";
import { PROVIDER_PRESETS } from "./providers";
import type { DesktopLlmProfileStatus } from "../types";
const draft = { baseUrl: "http://127.0.0.1:11500/v1", model: "qwen", apiKey: "", fastModel: "", strongModel: "" };
const profile: DesktopLlmProfileStatus = { configured: true, baseUrl: draft.baseUrl, model: "qwen", provider: "local", requiresKey: false };
it("local presets retain the user's custom port, model and entered credential", () => {
  const entered = {...draft, apiKey: "fixture-only"};
  for (const preset of PROVIDER_PRESETS.filter(p => !p.requiresKey)) expect(applyModelPreset(entered, preset)).toBe(entered);
});
it("switching to a different cloud endpoint never transfers the old key", () => {
  const next = applyModelPreset({...draft,apiKey:"fixture-only"}, PROVIDER_PRESETS[1]!);
  expect(next.apiKey).toBe(""); expect(next.baseUrl).toBe(PROVIDER_PRESETS[1]!.baseUrl);
});
it("clicking the same cloud preset preserves its configured model and key", () => {
  const preset = PROVIDER_PRESETS[1]!; const cloud = {...draft,baseUrl:preset.baseUrl,apiKey:"fixture-only"};
  expect(applyModelPreset(cloud,preset)).toBe(cloud);
});
it("old context and saved-key hints do not apply to another service", () => {
  expect(modelSettingsState(profile,{...draft,baseUrl:"https://example.com/v1"})).toMatchObject({sameService:false,sameModel:false,dirty:true,missing:"新云端服务需要填写 API Key。"});
});
it("model changes hide old context while same-service blank key may be retained", () => {
  expect(modelSettingsState(profile,{...draft,model:"other"})).toMatchObject({sameService:true,sameModel:false,dirty:true,missing:""});
});
it("routing and key edits are pending even when the primary model matches", () => {
  expect(modelSettingsState(profile,draft).dirty).toBe(false);
  for (const edit of [{fastModel:"small"},{strongModel:"large"},{apiKey:"fixture-only"}]) expect(modelSettingsState(profile,{...draft,...edit}).dirty).toBe(true);
  expect(modelSettingsState(profile,{...draft,model:" "}).missing).toContain("请填写模型名");
});
