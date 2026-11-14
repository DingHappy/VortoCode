// Production components and hooks, synthetic IPC only. Never installs or contacts a real service.
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { mockIPC } from "@tauri-apps/api/mocks";
import App from "../src/App";
import { RuntimeOnboarding } from "../src/components/RuntimeOnboarding";
import { OnboardingModel } from "../src/components/OnboardingModel";
import { useLlmProfile } from "../src/hooks/useLlmProfile";
import { modelSettingsState } from "../src/lib/modelSettings";
import type { DesktopLlmProfileStatus, DesktopProjectProfile } from "../src/types";
import type { LocalResidentStatus } from "../src/lib/localResident";
import { STORAGE_KEYS } from "../src/lib/storage";
import "../src/App.css";

const params = new URLSearchParams(location.search), scenario = params.get("scenario") || "new";
document.documentElement.dataset.theme = params.get("theme") || "light";
const calls: string[] = [], receipts: unknown[] = [];
const host: DesktopProjectProfile = { id: "qa-local-assistant", name: "这台 Mac 的助手", kind: "remote", repoRoot: "@assistant", baseUrl: "https://local.example", lastOpenedAt: 0, assistantPending: true };
let profile: DesktopLlmProfileStatus = { configured: scenario === "installed" || params.get("configured") === "1", baseUrl: "http://127.0.0.1:8000/v1", model: "fixture-model", requiresKey: false, provider: "local", revision: "qa-model-1" };
let local: LocalResidentStatus = { supported: scenario !== "unsupported", installed: scenario === "installed" || params.get("installed") === "1", state: scenario === "installed" || params.get("installed") === "1" ? "stopped" : "not_installed", availableVersion: "0.2.60", cronEnabled: false, revision: "qa-state-1", message: "合成环境，不操作真实服务。" };
let pendingRemote: DesktopProjectProfile | null = null;
if (scenario === "app-legacy") localStorage.setItem(STORAGE_KEYS.sid, "qa-legacy-chat");
if (scenario === "app-restore") {
  pendingRemote = { ...host, id: "qa-restored-assistant", assistantPending: false };
  localStorage.setItem(STORAGE_KEYS.runtimeSetup, JSON.stringify({ version: 1, status: "complete", targetId: pendingRemote.id }));
}
Object.assign(window, { onboardingQA: { calls, receipts, readLocal: () => ({ ...local }) } });
mockIPC(async (cmd, raw) => {
  calls.push(cmd);
  const args = (raw || {}) as Record<string, unknown>;
  switch (cmd) {
    case "desktop_smoke_ready": return false;
    case "get_llm_profile": if (params.get("modelRead") === "fail") throw new Error("合成本机模型读取失败"); return profile;
    case "set_llm_profile": receipts.push({ action: "save-model" }); profile = { ...profile, configured: true, baseUrl: String(args.baseUrl), model: String(args.model), revision: "qa-model-2" }; return profile;
    case "test_llm_connection": return { ok: true, models: ["fixture-model"], message: "已读取合成模型列表；未发送推理。" };
    case "local_resident_status": return { ...local };
    case "local_resident_install":
      receipts.push({ action: "install", cronEnabled: args.cronEnabled, copyModelProfile: args.copyModelProfile });
      if (scenario === "install-error") throw new Error("合成安装失败，原状态保留。");
      local = { ...local, installed: true, state: "stopped", version: "0.2.60", revision: "qa-state-2" }; return { ...local };
    case "local_resident_start": receipts.push({ action: "start" }); local = { ...local, state: "running", launchId: "qa-launch", projectId: host.id, revision: "qa-state-3" }; return { ...local };
    case "local_resident_connect": return host;
    case "remote_reachable": receipts.push({ action: "probe", target: args.projectId }); throw new Error("合成服务暂时无法连接，原位置保留。");
    case "remember_remote_project":
      receipts.push({ action: "register-remote", deferred: args.deferSelection === true });
      pendingRemote = { ...host, id: "qa-remote-assistant", name: "另一台电脑的助手", baseUrl: "https://remote.example", assistantPending: true }; return pendingRemote;
    case "list_desktop_projects": return pendingRemote ? [pendingRemote] : [];
    case "gateway_process_status": return { running: false, scope: "general", message: "合成未运行" };
    case "list_gateway_processes": case "list_gateway_recoveries": case "list_project_sessions": return [];
    case "start_gateway": receipts.push({ action: "unexpected-runtime-start" }); throw new Error("夹具禁止真实启动");
    case "get_gateway_recovery": return null;
    default: throw new Error(`Unsupported synthetic IPC: ${cmd}`);
  }
});
const noop = () => {};
function Fixture() {
  const llm = useLlmProfile(false, noop, async () => false);
  const [done, setDone] = useState(false);
  const draft = { baseUrl: llm.llmBaseInput, model: llm.llmModelInput, apiKey: llm.llmKeyInput, fastModel: llm.llmFastInput, strongModel: llm.llmStrongInput, roleModels: llm.llmRolesInput };
  if (done) return <main><h1>已进入合成个人空间</h1><p>这是测试回执，不代表真实安装或模型可用。</p></main>;
  return <RuntimeOnboarding initial={scenario === "restore-error" ? { version: 1, status: "complete", targetId: host.id } : { version: 1, status: "pending", choice: null }}
    restoreTarget={scenario === "restore-error" ? { ...host, assistantPending: false } : undefined}
    profiles={[]} modelReady={Boolean(llm.llmProfile?.configured && !modelSettingsState(llm.llmProfile, draft).dirty)} modelBusy={llm.llmProfileBusy}
    modelSetup={<OnboardingModel profile={llm.llmProfile} {...draft} loading={llm.llmProfileLoading} busy={llm.llmProfileBusy} error={llm.llmProfileError} conflict={llm.llmProfileConflict} note={llm.llmSaveNote}
      onBaseUrl={llm.setLlmBaseInput} onModel={llm.setLlmModelInput} onKey={llm.setLlmKeyInput} onSave={() => void llm.saveLlmProfile()} onReload={llm.reloadLlmProfile} onAccount={noop} />}
    onStatus={noop} onInspectAgain={noop} onConnect={async value => {
      receipts.push({ action: "connect", target: value.id });
      if (scenario === "restore-error" || scenario === "remote-error") throw new Error("合成连接不可用，未选择其他运行端。");
    }} onComplete={() => setDone(true)} />;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode>{scenario.startsWith("app") ? <App /> : <Fixture />}</React.StrictMode>);
