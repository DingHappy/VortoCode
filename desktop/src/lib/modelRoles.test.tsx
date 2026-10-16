import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelRoles } from "../components/settings/ModelRoles";
import { modelSettingsState, normalizedRoleModels, roleModelOptions } from "./modelSettings";
import type { DesktopLlmProfileStatus } from "../types";

const profile: DesktopLlmProfileStatus = { configured: true, baseUrl: "https://planner.example.test/v1", model: "planner", provider: "custom", requiresKey: true,
  models: ["planner", "small", "qwen:7b"], providers: [{ id: "worker", name: "Worker", baseUrl: "https://worker.example.test/v1", hasKey: true, models: ["cheap:tag"] }] };

it("offers registered channels and preserves literal model tags", () => {
  const options = roleModelOptions(profile).map(item => item.value);
  expect(options).toContain("default:planner"); expect(options).toContain("default:qwen:7b");
  expect(options).toContain("worker:cheap:tag"); expect(options).not.toContain("auto");
});
it("does not offer an unauthenticated remote channel", () => {
  const next = { ...profile, providers: [{ ...profile.providers![0], hasKey: false }] };
  expect(roleModelOptions(next).some(item => item.value.startsWith("worker:"))).toBe(false);
});
it("role-only edits are unsaved changes while blanks and order are equivalent", () => {
  const draft = { baseUrl: profile.baseUrl, model: profile.model, apiKey: "", fastModel: "", strongModel: "" };
  expect(modelSettingsState(profile, { ...draft, roleModels: { research: "worker:cheap" } }).dirty).toBe(true);
  expect(modelSettingsState(profile, { ...draft, roleModels: { research: "  " } }).dirty).toBe(false);
  expect(normalizedRoleModels({ review: " large ", research: "small" })).toEqual({ research: "small", review: "large" });
});
it("renders worker roles and the opt-in assessor with distinct defaults", () => {
  const html = renderToStaticMarkup(<ModelRoles profile={profile} value={{ research: "worker:cheap:tag" }} onChange={() => {}} />);
  for (const label of ["研究子 Agent", "实现子 Agent", "审查子 Agent", "模型评判器", "评判器留空关闭额外调用", "每家使用自己的 Key", "仅配置本机引擎"]) expect(html).toContain(label);
  expect(html).not.toContain("synthetic-worker-key");
});
