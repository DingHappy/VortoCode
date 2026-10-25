// Real SettingsModal + real useLlmProfile, deterministic IPC only. Never reads or writes user settings.
import { invoke } from "@tauri-apps/api/core";
import { ManagedRuntimes } from "../src/lib/managedRuntimes";
import { useLocalRuntimeControls } from "../src/hooks/useLocalRuntimeControls";
import { mockIPC } from "@tauri-apps/api/mocks";
import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { SettingsModal, type SettingsSection } from "../src/components/SettingsModal";
import { useLlmProfile } from "../src/hooks/useLlmProfile";
import type { DesktopLlmProfileStatus, TrustStatus, GatewayProcessStatus, WorkspaceScope } from "../src/types";
import "../src/App.css";

const params = new URLSearchParams(location.search);
document.documentElement.setAttribute("data-theme", params.get("theme") || "dark");
const lifecycleFixture = Boolean(params.get("runtime"));
const fixtureScope: WorkspaceScope = params.get("scope") === "scratch" ? "scratch" : params.get("scope") === "project" ? "project" : "general";
const fixtureRoot = fixtureScope === "scratch" ? "/fixture/qa-workspace" : fixtureScope === "project" ? "/fixture/project" : "/fixture/general";
let nativeRuntime: GatewayProcessStatus = { running: params.get("runtime") === "running", runtimeId: fixtureScope,
  launchId: params.get("runtime") === "running" ? "qa-launch-1" : undefined, scope: fixtureScope,
  workspaceId: fixtureScope === "scratch" ? "qa-workspace" : undefined,
  workspaceRoot: fixtureRoot, repoRoot: fixtureScope === "project" ? fixtureRoot : undefined,
  baseUrl: "http://127.0.0.1:8080", message: "合成运行端" };
let fixtureLaunches = 1;
let fixtureActivityBusy = params.get("activity") === "busy";
let fixtureActivityDeferred = params.get("activity") === "defer";
const launchedWorkspaces: Array<{ scope: string; workspaceId: string }> = [];
const guest = params.get("state") === "guest";
let receiptFails = params.get("receipt") === "fail";
let receiptDeferred = params.get("receipt") === "defer";
let profile: DesktopLlmProfileStatus = {
  revision: "qa-revision-1",
  configured: !guest, provider: "local", requiresKey: false,
  baseUrl: guest ? "" : "http://127.0.0.1:8000/v1", model: guest ? "" : "qwen3.5-35b-a3b",
  models: ["qwen3.5-35b-a3b", "qwen3.5-9b"], providers: [], roleModels: {}, configPath: "~/.vortocode/llm-profile.json"
};
if (params.get("save") === "unchanged") profile.roleModels = { research: "default:research-model" };
if (params.get("providers") === "1") profile.providers = [
  { id: "alpha", name: "Alpha", baseUrl: "https://alpha.example.test/v1", hasKey: true, models: ["alpha-model"] },
  { id: "beta", name: "Beta", baseUrl: "https://beta.example.test/v1", hasKey: true, models: ["beta-model"] },
];
let confirmationsAllowed = params.get("confirm") !== "cancel";
const probeTargets: Array<{ providerId: string | null; baseUrl: string; explicitKey: boolean }> = [];
let instructions = "回答用中文，报告任务进展和需要我决定的事项。";
let desktop = { target: "ssh:vorto-97", sshConnected: false, viewerUrl: null, computer: null, checkedAt: null, error: null };
let browser = { enabled: false, browserPath: "/Applications/Chromium.app" };
let control = { enabled: params.get("desktopEnabled") === "1", target: "ssh:vorto-97", apps: "firefox, mousepad" };
let experimental = false;
let reads = 0;
let restarts = 0;
const settingReads = new Map<string, number>();
const settingRequests: Array<{ command: string; enabled?: boolean; target?: string; apps?: string }> = [];
function settingRead<T>(kind: string, value: T): Promise<T> | T {
  const count = (settingReads.get(kind) || 0) + 1; settingReads.set(kind, count);
  if (params.get("settingRead") === "fail-once" && count === 1) throw new Error(`模拟${kind}读取失败`);
  if (params.get("settingRead") === "defer" && count === 1) return new Promise(resolve => Object.assign(window, { [`releaseSettingRead_${kind}`]: () => resolve(value) }));
  return value;
}
function settingWrite<T>(kind: string, persist: () => T): Promise<T> | T {
  if (params.get("settingWrite") === "cancel") throw new Error("模拟原生确认已取消，配置未改变");
  if (params.get("settingWrite") === "fail") throw new Error("模拟配置保存失败");
  if (params.get("settingWrite") === "defer") return new Promise(resolve => Object.assign(window, { [`releaseSettingWrite_${kind}`]: () => resolve(persist()) }));
  return persist();
}
const calls: string[] = [];
Object.assign(window, { settingsQACalls: calls, isTauri: true, settingsQAProbeTargets: probeTargets, settingsQASettingRequests: settingRequests, settingsQARestarts: () => restarts,
  settingsQAStoredPermissions: () => ({ browserEnabled: browser.enabled, desktopEnabled: control.enabled, desktopTarget: control.target, desktopApps: control.apps }),
  settingsQAExternalProfile: () => { profile = { ...profile, revision: "qa-latest-after-preview", model: "latest-after-preview" }; },
  settingsQAStoredProfile: () => ({ revision: profile.revision, model: profile.model }),
  settingsQARuntimeBusy: (busy: boolean) => { fixtureActivityBusy = busy; },
  settingsQALaunchedWorkspaces: launchedWorkspaces,
  settingsQAReceiptFail: (fail: boolean) => { receiptFails = fail; },
  settingsQAConfirm: (allow: boolean) => { confirmationsAllowed = allow; } });
mockIPC(async (cmd, raw) => {
  const args = (raw ?? {}) as Record<string, string | boolean | Record<string, string>>;
  calls.push(cmd);
  switch (cmd) {
    case "gateway_process_status": return nativeRuntime;
    case "list_gateway_processes": return nativeRuntime.running ? [nativeRuntime] : [];
    case "get_gateway_recovery": return null;
    case "list_gateway_recoveries": return [];
    case "stop_gateway": {
      if (args.expectedLaunchId && args.expectedLaunchId !== nativeRuntime.launchId) throw new Error("启动身份已变化");
      nativeRuntime = { running: false, message: "合成引擎已停止" };
      return nativeRuntime;
    }
    case "start_gateway": {
      launchedWorkspaces.push({ scope: String(args.scope), workspaceId: String(args.workspaceId) });
      nativeRuntime = { running: true, runtimeId: String(args.scope), scope: args.scope as WorkspaceScope,
        launchId: `qa-launch-${++fixtureLaunches}`, workspaceId: String(args.scope) === "scratch" ? String(args.workspaceId) : undefined,
        workspaceRoot: fixtureRoot, repoRoot: fixtureScope === "project" ? fixtureRoot : undefined,
        baseUrl: "http://127.0.0.1:8080", message: "合成引擎已就绪" };
      return nativeRuntime;
    }
    case "account_status": return { signedIn: false, email: "", deviceName: "", offline: false, manageUrl: "", note: "" };
    case "get_llm_profile": {
      reads++;
      if (params.get("recoveryRead") === "fail-once" && reads === 2) throw new Error("模拟最新配置读取失败");
      if (params.get("read") === "fail-once" && reads === 1) throw new Error("模拟本机配置读取失败");
      if (["stale", "stale-error"].includes(params.get("read") || "") && reads === 1) {
        const stale = { ...profile, model: "stale-model" };
        return new Promise((resolve, reject) => Object.assign(window, { releaseSettingsStaleRead: () => params.get("read") === "stale-error" ? reject(new Error("过期读取失败")) : resolve(stale) }));
      }
      return profile;
    }
    case "get_model_configuration_state": {
      if (lifecycleFixture) return { schemaVersion: 1, savedRevision: profile.revision,
        runtimes: nativeRuntime.running ? [{ runtimeId: nativeRuntime.runtimeId, scope: fixtureScope, status: "applied" }] : [] };
      if (receiptFails) throw new Error("模拟回执读取失败");
      if (receiptDeferred) {
        receiptDeferred = false;
        const staleRevision = profile.revision;
        return new Promise(resolve => Object.assign(window, { releaseConfigurationReceipt: () => resolve({ schemaVersion: 1, savedRevision: staleRevision, runtimes: [] }) }));
      }
      return { schemaVersion: 1, savedRevision: params.get("receipt") === "stale" ? "qa-old" : profile.revision,
        runtimes: params.get("receipt") === "none" ? [] : [
          { runtimeId: "qa-general", scope: "general", status: "applied" },
          { runtimeId: "qa-project", scope: "project", status: "pending" },
          { runtimeId: "qa-legacy", scope: "scratch", status: "unknown" },
        ] };
    }
    case "refresh_llm_providers": return profile;
    case "confirm_action": return confirmationsAllowed;
    case "test_llm_connection": {
      const providerId = args.providerId == null ? null : String(args.providerId);
      probeTargets.push({ providerId, baseUrl: String(args.baseUrl), explicitKey: Boolean(args.apiKey) });
      return { ok: true, status: 200, modelCount: 1, modelAvailable: null, models: [`${providerId || "default"}-model`], message: "合成验收：模型列表可达" };
    }
    case "save_llm_provider": {
      if (params.get("providerSave") === "conflict" && profile.revision === "qa-revision-1") {
        profile = { ...profile, revision: "qa-external-provider", model: "externally-updated-model" };
        throw { code: "model_configuration_conflict", message: "模型配置已被其他操作更新；本次没有写入。当前草稿仍保留" };
      }
      if (args.expectedRevision && args.expectedRevision !== profile.revision) throw { code: "model_configuration_conflict", message: "本次没有写入，当前草稿仍保留" };
      if (params.get("providerSave") === "fail") throw new Error("模拟服务保存失败");
      const id = String(args.id).trim().toLowerCase();
      const saved = { id, name: String(args.name).trim() || id, baseUrl: String(args.baseUrl).trim().replace(/\/+$/, ""), hasKey: true, models: [id + "-model"] };
      const persist = () => {
        profile = { ...profile, providers: [...(profile.providers ?? []).filter(item => item.id !== id), saved] };
        return profile;
      };
      if (params.get("providerSave") === "defer") return new Promise(resolve => Object.assign(window, { releaseSettingsProviderSave: () => resolve(persist()) }));
      return persist();
    }
    case "set_llm_profile": {
      if (params.get("save") === "conflict" && profile.revision === "qa-revision-1") {
        profile = { ...profile, revision: "qa-external-default", model: "externally-updated-model" };
        throw { code: "model_configuration_conflict", message: "模型配置已被其他操作更新；本次没有写入。当前草稿仍保留" };
      }
      if (args.expectedRevision && args.expectedRevision !== profile.revision) throw { code: "model_configuration_conflict", message: "本次没有写入，当前草稿仍保留" };
      if (params.get("save") === "unchanged") return profile;
      if (params.get("save") === "fail") throw new Error("模拟保存失败，草稿仍保留");
      if (params.get("save") === "cancel") throw new Error("操作已取消");
      profile = {
        ...profile, revision: "qa-revision-saved", configured: true, baseUrl: String(args.baseUrl), model: String(args.model),
        fastModel: args.fastModel as string || undefined, strongModel: args.strongModel as string || undefined, roleModels: args.roleModels as Record<string, string>
      };
      return profile;
    }
    case "get_experimental_workers": return experimental;
    case "set_experimental_workers": experimental = Boolean(args.enabled); return experimental;
    case "get_update_check_enabled": return true;
    case "get_user_instructions": return settingRead("instructions", { path: "~/.vortocode/AGENTS.md", content: instructions, exists: true });
    case "set_user_instructions": {
      const content = String(args.content);
      settingRequests.push({ command: cmd });
      return settingWrite("instructions", () => { instructions = content; return { path: "~/.vortocode/AGENTS.md", content: instructions, exists: true }; });
    }
    case "get_browser_control": return settingRead("browser", browser);
    case "set_browser_control": {
      const enabled = Boolean(args.enabled); settingRequests.push({ command: cmd, enabled });
      return settingWrite("browser", () => { browser = { ...browser, enabled }; return browser; });
    }
    case "get_desktop_control": return settingRead("desktop", control);
    case "set_desktop_control": {
      const next = { enabled: Boolean(args.enabled), target: String(args.target), apps: String(args.apps) };
      settingRequests.push({ command: cmd, ...next });
      return settingWrite("desktop", () => { control = next; return control; });
    }
    case "get_resident_desktop": case "refresh_resident_desktop": case "check_resident_desktop": return desktop;
    case "save_resident_desktop_target": desktop = { ...desktop, target: `ssh:${String(args.target).replace(/^ssh:/, "")}` }; return desktop;
    case "plugin:event|listen": return 1;
    default: return null;
  }
}, { shouldMockEvents: true });
function Fixture() {
  const [open, setOpen] = useState(true);
  const [notice, setNotice] = useState("");
  const onBanner = useCallback((text: string) => setNotice(text), []);
  const restart = useCallback(async () => {
    restarts++;
    if (params.get("restart") === "throw") throw new Error("模拟重启失败");
    return params.get("restart") === "ok";
  }, []);
  const llm = useLlmProfile(open, onBanner, restart);
  const [process, setProcess] = useState(nativeRuntime);
  const [runtimeConnected, setRuntimeConnected] = useState(params.get("runtime") === "running");
  const [remote, setRemote] = useState(params.get("runtime") === "remote");
  const [fixtureClient] = useState(() => ({}));
  const [manager] = useState(() => new ManagedRuntimes(invoke, { setProcessStatus: setProcess,
    setRuntimeProcesses: () => {}, setRuntimeRecoveries: () => {}, setRecoveryRecord: () => {} }));
  const runtimeControls = useLocalRuntimeControls({
    processStatus: process, scope: fixtureScope, runtime: { scope: fixtureScope, workdir: fixtureRoot },
    repoRoot: fixtureScope === "project" ? fixtureRoot : "", baseUrl: "http://127.0.0.1:8080", sid: "qa-chat-session", token: "",
    remote: () => remote, starting: () => false, switching: () => false, client: () => fixtureClient, runtimes: manager,
    disconnect: async () => { setRuntimeConnected(false); },
    startWorkspace: async request => {
      await manager.launch({ scope: request.scope, repoRoot: request.repoRoot ?? null, workspaceId: request.workspaceId ?? request.sid,
        port: 8080, token: request.token });
      setRuntimeConnected(true);
      return true;
    },
    readActivity: async status => {
      if (params.get("activity") === "fail") throw new Error("模拟活动接口不可用");
      if (fixtureActivityDeferred) {
        fixtureActivityDeferred = false;
        await new Promise<void>(resolve => Object.assign(window, { releaseRuntimeActivity: resolve }));
      }
      return { version: 1, ready: true, launch_id: status.launchId, scope: status.scope, workspace_root: status.workspaceRoot,
        counts: { turns: 0, queued_inputs: 0, confirmations: 0, tasks: fixtureActivityBusy ? 1 : 0, commands: 0, terminals: 0, writes: 0 } };
    },
    onBanner,
  });
  useEffect(() => {
    Object.assign(window, { applyFreshSettingsProfile: () => {
      profile = { ...profile, model: "fresh-updated-model" };
      llm.applyLlmProfile(profile);
    } });
    return () => { Reflect.deleteProperty(window, "applyFreshSettingsProfile"); };
  }, [llm.applyLlmProfile]);
  const [trust, setTrust] = useState<TrustStatus>({ level: "ask", effective: "ask", ceiling: "reads", levels: ["ask", "reads", "full"], capability_profile: "local", workspace_scope: "project", local_file_reads: true, supervised: true });
  const noop = () => { };
  return <><main data-profile-checked={llm.llmProfileChecked} data-profile-loading={llm.llmProfileLoading} style={{ padding: 24 }}><p>真实设置组件 · 合成数据验收 · 不连接真实服务</p><button onClick={() => setOpen(true)}>打开设置</button><p role="status">{notice}</p></main>
    {open && <SettingsModal activeScope={lifecycleFixture ? fixtureScope : "project"} connection={runtimeConnected ? "connected" : "disconnected"} connectionText="未连接" connectionNote="" runtimeStarting={false} projectSwitching={false}
      processStatus={lifecycleFixture ? runtimeControls.target : { running: false, message: "本机引擎未运行" }}
      runtimeIsRemote={remote} runtimeActionBusy={runtimeControls.busy} runtimeActionNote={runtimeControls.note} onReloadRuntime={runtimeControls.reload} recoveryRecord={null} llmProfile={llm.llmProfile}
      llmBaseInput={llm.llmBaseInput} llmModelInput={llm.llmModelInput} llmKeyInput={llm.llmKeyInput} llmFastInput={llm.llmFastInput} llmStrongInput={llm.llmStrongInput}
      llmRolesInput={llm.llmRolesInput} onLlmRolesChange={llm.setLlmRolesInput} llmProfileBusy={llm.llmProfileBusy}
      repoRoot="/Users/demo/projects/workspace" baseUrl="http://127.0.0.1:8080" token="" modelPicks={null} initialSection={(params.get("section") || "computer") as SettingsSection}
      onClose={() => setOpen(false)} onLlmBaseChange={llm.setLlmBaseInput} onLlmModelChange={llm.setLlmModelInput} onLlmKeyChange={llm.setLlmKeyInput}
      onLlmFastChange={llm.setLlmFastInput} onLlmStrongChange={llm.setLlmStrongInput} onRestartRuntime={restart} onLlmProfileChange={llm.applyLlmProfile}
      onModelPicksChange={noop} onSaveLlmProfile={llm.saveLlmProfile} onClearLlmProfile={llm.clearLlmProfile} llmSaveNote={llm.llmSaveNote}
      llmProfileConflict={llm.llmProfileConflict} llmSaveKind={llm.llmSaveKind} llmProfileLoading={llm.llmProfileLoading} llmProfileError={llm.llmProfileError} onReloadLlmProfile={llm.reloadLlmProfile}
      hasResident residentName="vorto-97" residentConnected={false} onOpenResidentSetup={() => setNotice("常驻连接入口")} onOpenSchedule={() => setNotice("计划入口")}
      onEditAssistant={() => setNotice("资料入口")} onOpenPlugins={() => setNotice("插件入口")}
      onChooseRepo={noop} onDismissRecovery={noop} onRestoreRecovery={noop} onBaseUrlChange={noop} onTokenChange={noop} onStopRuntime={runtimeControls.stop}
      onConnectExisting={noop} onStartRuntime={runtimeControls.start} onSwitchScope={() => setRemote(false)} trust={trust} trustBusy={false} onTrustChange={level => setTrust({ ...trust, level, effective: level })} />}</>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
