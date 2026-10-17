import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelRoles } from "../components/settings/ModelRoles";
import { externalWorkerOptions, modelSettingsState, normalizedRoleModels, roleModelOptions } from "./modelSettings";
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

const workers = [{ worker: "codex", label: "Codex CLI（本机）", path: "/nvm/bin/codex", loggedIn: true, auth: "Logged in using ChatGPT" },
  { worker: "claude", label: "Claude Code CLI（本机）", path: null }];

it("offers local CLIs only to implement and review, with install status", () => {
  const html = renderToStaticMarkup(<ModelRoles profile={profile} value={{ implement: "cli:codex", review: "cli:claude" }}
    onChange={() => {}} workers={workers} />);
  expect(html).toContain("已就绪：/nvm/bin/codex · Logged in using ChatGPT");
  expect(html).toContain("本机没找到 claude 命令");
  expect(html).toContain("CLI 拿不到 GitHub 令牌");
  const cliList = html.slice(html.indexOf('id="llm-role-cli-options"'));
  expect(cliList).toContain('value="cli:codex"');
  const modelList = html.slice(html.indexOf('id="llm-role-model-options"'), html.indexOf('id="llm-role-cli-options"'));
  expect(modelList).not.toContain("cli:");                 // 研究 / 评判器的候选里没有外部 CLI
  expect(html.match(/list="llm-role-cli-options"/g)).toHaveLength(2);
});
it("labels CLI options by detection result and treats them as role edits", () => {
  expect(externalWorkerOptions(workers).map(item => item.label)).toEqual(["Codex CLI（本机） · 已就绪", "Claude Code CLI（本机） · 未找到"]);
  expect(externalWorkerOptions([{ ...workers[0], loggedIn: false }])[0].label).toContain("未登录");
  expect(externalWorkerOptions(null).map(item => item.value)).toEqual(["cli:codex", "cli:claude"]);
  const draft = { baseUrl: profile.baseUrl, model: profile.model, apiKey: "", fastModel: "", strongModel: "" };
  expect(modelSettingsState(profile, { ...draft, roleModels: { implement: "cli:codex" } }).dirty).toBe(true);
});

it("tells the user how to log in when the CLI is installed but signed out", () => {
  const html = renderToStaticMarkup(<ModelRoles profile={profile} value={{ implement: "cli:claude" }} onChange={() => {}}
    workers={[{ worker: "claude", label: "Claude Code CLI（本机）", path: "/bin/claude", loggedIn: false }]} />);
  expect(html).toContain("但未登录：在终端运行 claude auth login");
});
