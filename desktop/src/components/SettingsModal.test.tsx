import { expect, it } from "vitest";

import { relayMode, settingsBlockVisible } from "./SettingsModal";

it("only shows the relay's account and usage blocks to people actually using that relay", () => {
  expect(relayMode(null)).toBe(false);
  expect(relayMode({ provider: "custom" })).toBe(false);
  expect(relayMode({ provider: "local" })).toBe(false);
  expect(relayMode({ provider: "vortocode" })).toBe(true);
  expect(relayMode({ provider: "custom", account: { username: "me" } })).toBe(true);
});

it("shows the legacy relay login only to people who used it, not to members", () => {
  expect(settingsBlockVisible("relayLogin", { provider: "vortocode" })).toBe(false);
  expect(settingsBlockVisible("relayLogin", { provider: "vortocode", account: { keySource: "token_plan" } })).toBe(true);
  expect(settingsBlockVisible("relayLogin", { provider: "vortocode", account: { keySource: "vortocode_member" } })).toBe(false);
  expect(settingsBlockVisible("usage", { provider: "vortocode", account: { keySource: "vortocode_member" } })).toBe(true);
  expect(settingsBlockVisible("usage", { provider: "custom" })).toBe(false);
});

// Rendering the real page composition catches missing save controls and status conflation.
import { renderToStaticMarkup } from "react-dom/server";
import type { ComponentProps } from "react";
import { SettingsModal } from "./SettingsModal";
const noop = () => {};
function renderSettings(overrides: Partial<ComponentProps<typeof SettingsModal>> = {}) {
  return renderToStaticMarkup(<SettingsModal activeScope="project" connection="connected" connectionText="常驻实例已连接" connectionNote=""
    runtimeStarting={false} projectSwitching={false} processStatus={{ running: false, message: "未运行" }} recoveryRecord={null}
    llmProfile={{ configured: true, baseUrl: "http://127.0.0.1:8000/v1", model: "fixture-model", provider: "local", requiresKey: false }}
    llmBaseInput="http://127.0.0.1:8000/v1" llmModelInput="fixture-model" llmKeyInput="" llmFastInput="" llmStrongInput="" llmRolesInput={{}} onLlmRolesChange={noop}
    llmProfileBusy={false} repoRoot="/fixture" baseUrl="http://127.0.0.1:8080" token="" modelPicks={null}
    onClose={noop} onLlmBaseChange={noop} onLlmModelChange={noop} onLlmFastChange={noop} onLlmStrongChange={noop} onLlmKeyChange={noop}
    onRestartRuntime={async () => false} onLlmProfileChange={noop} onModelPicksChange={noop} onSaveLlmProfile={noop} onClearLlmProfile={noop}
    onChooseRepo={noop} onDismissRecovery={noop} onRestoreRecovery={noop} onBaseUrlChange={noop} onTokenChange={noop} onStopRuntime={noop}
    onConnectExisting={noop} onStartRuntime={noop} onSwitchScope={noop} trust={null} trustBusy={false} onTrustChange={noop} {...overrides}/>);
}
it("provides a save action for role edits without opening the default-service editor", () => {
  const markup = renderSettings({ initialSection: "model", llmRolesInput: { implement: "default:other-model" } });
  expect(markup).toContain('aria-label="实现子 Agent"');
  expect(markup).not.toContain('aria-label="默认服务设置"');
  const save = markup.match(/<button[^>]*>保存模型配置<\/button>/)?.[0];
  expect(save).toBeDefined();
  expect(save).not.toContain("disabled");
  expect(markup).toContain("有未保存的修改");
});
it("does not report the local runtime running just because the remote conversation connected", () => {
  const markup = renderSettings({ initialSection: "computer", hasResident: true, residentConnected: true, residentName: "fixture-resident" });
  expect(markup).toContain("当前对话已连接到常驻实例");
  expect(markup).toContain("本机引擎未运行");
  expect(markup).not.toContain("本机引擎运行中");
});
it("opens the legacy abilities entry at permissions and exposes seven clear categories", () => {
  const markup = renderSettings({ initialSection: "abilities" });
  expect(markup).toContain('id="settings-page-title">权限与自动化');
  for (const label of ["账号与用量", "模型与执行", "电脑与连接", "工具与集成", "个性化", "高级与关于"]) expect(markup).toContain(label);
  expect(markup).toContain("授权级别按工作区保存");
});
