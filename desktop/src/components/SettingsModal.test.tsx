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
it("separates local and remote resident cards while retaining the original remote anchor", () => {
  const markup = renderSettings({ initialSection: "computer" });
  expect(markup).toContain('id="setting-local-resident"');
  expect(markup).toContain('id="setting-resident"');
  expect(markup).toContain('本机常驻助理');
  expect(markup).toContain('其他电脑上的常驻助理');
  expect(markup).toContain('本机引擎与工作区');
});
it("does not present the registered local service as another computer", () => {
  const markup = renderSettings({ initialSection: "computer", hasResident: true, residentConnected: true,
    residentName: "local-fixture-name", residentAccountProjectId: "local-fixture", localResidentProjectId: "local-fixture", selectedAssistantHostId: "local-fixture" });
  expect(markup).toContain('尚未登记其他常驻电脑');
  expect(markup).not.toContain('local-fixture-name');
  expect(markup).not.toContain('当前对话已连接到常驻实例');
});
it("keeps remote registration unknown until the local resident identity is checked", () => {
  const markup = renderSettings({ initialSection: "computer", hasResident: true, residentName: "unchecked-host", residentRegistrationKnown: false,
    onConnectResident: noop, onOpenResidentSetup: noop });
  expect(markup).toContain("正在核对已登记的常驻电脑");
  expect(markup).not.toContain("尚未登记其他常驻电脑");
  expect(markup).not.toContain("unchecked-host");
  expect(markup.match(/<button[^>]*>连接助手<\/button>/)?.[0]).toContain("disabled");
  expect(markup.match(/<button[^>]*>连接常驻电脑<\/button>/)?.[0]).not.toContain("disabled");
});
it("describes an exact local resident connection without offering temporary controls over it", () => {
  const markup = renderSettings({ initialSection: "computer", runtimeIsRemote: true, localResidentProjectId: "local-fixture", selectedAssistantHostId: "local-fixture" });
  expect(markup).toContain("当前对话使用本机常驻助理；下方仅管理临时引擎");
  expect(markup).toContain("退出 Desktop 后停止；不开定时任务");
  expect(markup).not.toContain("当前会话连接到远端实例");
  expect(markup).not.toContain("远端会话；此处不控制服务器");
  expect(markup).not.toContain(">停止本机引擎</button>");
  expect(markup).not.toContain(">重新加载本机引擎</button>");
});
it("opens the legacy abilities entry on its original permissions tab in compact groups", () => {
  const markup = renderSettings({ initialSection: "abilities" });
  expect(markup).toContain('id="settings-page-title" tabindex="-1">权限与自动化');
  for (const label of ["你的账号", "个人空间", "模型与执行", "连接与工具", "高级与关于"]) expect(markup).toContain(label);
  expect(markup).toContain("当前目标尚未连接");
});
it("embeds settings in the workspace with the same save action and target warning", () => {
  const markup = renderSettings({ presentation: "page", initialSection: "model", llmRolesInput: { implement: "default:draft" } });
  expect(markup).not.toContain("<dialog");
  expect(markup).toContain('<section class="settings-modal settings-layout settings-redesign settings-workspace-page" aria-label="设置"');
  expect(markup).toContain("有未保存的修改");
  expect(markup.match(/<button[^>]*>保存模型配置<\/button>/)?.[0]).not.toContain("disabled");
  expect(renderSettings({ presentation: "page", initialSection: "permissions" })).toContain("当前目标尚未连接");
});
it("shows a retryable read error without claiming an unknown profile is empty", () => {
  const markup = renderSettings({ initialSection: "model", llmProfile: null, llmProfileError: "读取失败", onReloadLlmProfile: noop });
  expect(markup).toContain("暂时无法读取本机模型配置");
  expect(markup).toContain("重试读取");
  expect(markup).not.toContain("还没有模型服务");
  expect(markup.match(/<button[^>]*>保存模型配置<\/button>/)?.[0]).toContain("disabled");
});
it("blocks saving a previously loaded draft while a new profile read is pending", () => {
  const markup = renderSettings({ initialSection: "model", llmProfileLoading: true, llmRolesInput: { implement: "default:draft" } });
  expect(markup).toContain("正在读取本机模型配置");
  expect(markup.match(/<button[^>]*>保存模型配置<\/button>/)?.[0]).toContain("disabled");
});
it("links an unsaved model draft back to its page and describes the sidebar indicator", () => {
  const markup = renderSettings({ initialSection: "model", llmRolesInput: { implement: "default:draft" } });
  expect(markup).toContain('aria-label="前往默认模型与分工"');
  expect(markup).toContain('aria-describedby="settings-pending-model"');
});
it("does not show the previous model save result as the current account status", () => {
  const markup = renderSettings({ initialSection: "account", llmSaveKind: "pending", llmSaveNote: "配置已保存，等待重启" });
  expect(markup).not.toContain("配置已保存，等待重启");
});
it("pauses old runtime receipts and repeated saving until a configuration conflict is resolved", () => {
  const markup = renderSettings({ initialSection: "model", llmProfileConflict: true, llmRolesInput: { implement: "default:draft" } });
  expect(markup).toContain('aria-label="模型配置冲突处理"');
  expect(markup).toContain("引擎生效状态待核对");
  expect(markup).not.toContain('aria-label="模型配置生效状态"');
  expect(markup.match(/<button[^>]*>保存模型配置<\/button>/)?.[0]).toContain("disabled");
  expect(markup).toContain('value="default:draft"');
});
it("offers a direct start for an idle General engine without creating a Scratch", () => {
  const markup = renderSettings({ initialSection: "computer", activeScope: "general", repoRoot: "", connection: "disconnected" });
  expect(markup).toContain("本机通用工作区");
  expect(markup.match(/<button[^>]*>启动本机引擎<\/button>/)?.[0]).not.toContain("disabled");
  expect(markup).not.toContain(">停止本机引擎</button>");
});
it("offers guarded reload and stop for an owned engine, with activity feedback", () => {
  const markup = renderSettings({ initialSection: "computer", processStatus: { running: true, runtimeId: "general", launchId: "launch-test", message: "" }, onReloadRuntime: noop });
  expect(markup.match(/<button[^>]*>重新加载本机引擎<\/button>/)?.[0]).not.toContain("disabled");
  expect(markup.match(/<button[^>]*>停止本机引擎<\/button>/)?.[0]).not.toContain("disabled");
  expect(markup).toContain("仍有活动或无法确认时保留引擎");
});
it("does not expose local stop/start controls for remote conversations", () => {
  const markup = renderSettings({ initialSection: "computer", runtimeIsRemote: true, connection: "disconnected" });
  expect(markup).toContain("切到本机通用工作区");
  expect(markup).not.toContain(">启动工作区</button>");
  expect(markup).not.toContain("高级连接设置");
  expect(markup).not.toContain(">启动本机引擎</button>");
  expect(markup).not.toContain(">停止本机引擎</button>");
  expect(markup).not.toContain(">重新加载本机引擎</button>");
});
it("keeps lifecycle controls and closing disabled while activity is being checked", () => {
  const markup = renderSettings({ initialSection: "computer", activeScope: "general", runtimeActionBusy: true });
  expect(markup).toContain("正在核对引擎活动");
  expect(markup.match(/<button[^>]*>新建 Scratch<\/button>/)?.[0]).toContain("disabled");
  expect(markup.match(/<button[^>]*>选择 Git 项目<\/button>/)?.[0]).toContain("disabled");
  expect(markup.match(/<button[^>]*>启动本机引擎<\/button>/)?.[0]).toContain("disabled");
});
