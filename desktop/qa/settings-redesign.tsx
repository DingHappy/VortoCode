// Real SettingsModal + real useLlmProfile, deterministic IPC only. Never reads or writes user settings.
import { mockIPC } from "@tauri-apps/api/mocks";
import { useCallback, useState } from "react";
import { createRoot } from "react-dom/client";
import { SettingsModal, type SettingsSection } from "../src/components/SettingsModal";
import { useLlmProfile } from "../src/hooks/useLlmProfile";
import type { DesktopLlmProfileStatus, TrustStatus } from "../src/types";
import "../src/App.css";

const params = new URLSearchParams(location.search);
document.documentElement.setAttribute("data-theme", params.get("theme") || "dark");
const guest = params.get("state") === "guest";
let profile: DesktopLlmProfileStatus = {
  configured: !guest, provider: "local", requiresKey: false,
  baseUrl: guest ? "" : "http://127.0.0.1:8000/v1", model: guest ? "" : "qwen3.5-35b-a3b",
  models: ["qwen3.5-35b-a3b", "qwen3.5-9b"], providers: [], roleModels: {}, configPath: "~/.vortocode/llm-profile.json"
};
let instructions = "回答用中文，报告任务进展和需要我决定的事项。";
let desktop = { target: "ssh:vorto-97", sshConnected: false, viewerUrl: null, computer: null, checkedAt: null, error: null };
let browser = { enabled: false, browserPath: "/Applications/Chromium.app" };
let control = { enabled: false, target: "ssh:vorto-97", apps: "firefox, mousepad" };
let experimental = false;
const calls: string[] = [];
Object.assign(window, { settingsQACalls: calls, isTauri: true });
mockIPC(async (cmd, raw) => {
  const args = (raw ?? {}) as Record<string, string | boolean | Record<string, string>>;
  calls.push(cmd);
  switch (cmd) {
    case "account_status": return { signedIn: false, email: "", deviceName: "", offline: false, manageUrl: "", note: "" };
    case "get_llm_profile": case "refresh_llm_providers": return profile;
    case "set_llm_profile": {
      if (params.get("save") === "fail") throw new Error("模拟保存失败，草稿仍保留");
      if (params.get("save") === "cancel") throw new Error("操作已取消");
      profile = {
        ...profile, configured: true, baseUrl: String(args.baseUrl), model: String(args.model),
        fastModel: args.fastModel as string || undefined, strongModel: args.strongModel as string || undefined, roleModels: args.roleModels as Record<string, string>
      };
      return profile;
    }
    case "get_experimental_workers": return experimental;
    case "set_experimental_workers": experimental = Boolean(args.enabled); return experimental;
    case "get_update_check_enabled": return true;
    case "get_user_instructions": return { path: "~/.vortocode/AGENTS.md", content: instructions, exists: true };
    case "set_user_instructions": instructions = String(args.content); return { path: "~/.vortocode/AGENTS.md", content: instructions, exists: true };
    case "get_browser_control": return browser;
    case "set_browser_control": browser = { ...browser, enabled: Boolean(args.enabled) }; return browser;
    case "get_desktop_control": return control;
    case "set_desktop_control": control = { enabled: Boolean(args.enabled), target: String(args.target), apps: String(args.apps) }; return control;
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
  const restart = useCallback(async () => params.get("restart") === "ok", []);
  const llm = useLlmProfile(open, onBanner, restart);
  const [trust, setTrust] = useState<TrustStatus>({ level: "ask", effective: "ask", ceiling: "reads", levels: ["ask", "reads", "full"], capability_profile: "local", workspace_scope: "project", local_file_reads: true, supervised: true });
  const noop = () => { };
  return <><main style={{ padding: 24 }}><p>真实设置组件 · 合成数据验收 · 不连接真实服务</p><button onClick={() => setOpen(true)}>打开设置</button><p role="status">{notice}</p></main>
    {open && <SettingsModal activeScope="project" connection="disconnected" connectionText="未连接" connectionNote="" runtimeStarting={false} projectSwitching={false}
      processStatus={{ running: false, message: "本机引擎未运行" }} recoveryRecord={null} llmProfile={llm.llmProfile}
      llmBaseInput={llm.llmBaseInput} llmModelInput={llm.llmModelInput} llmKeyInput={llm.llmKeyInput} llmFastInput={llm.llmFastInput} llmStrongInput={llm.llmStrongInput}
      llmRolesInput={llm.llmRolesInput} onLlmRolesChange={llm.setLlmRolesInput} llmProfileBusy={llm.llmProfileBusy}
      repoRoot="/Users/demo/projects/workspace" baseUrl="http://127.0.0.1:8080" token="" modelPicks={null} initialSection={(params.get("section") || "computer") as SettingsSection}
      onClose={() => setOpen(false)} onLlmBaseChange={llm.setLlmBaseInput} onLlmModelChange={llm.setLlmModelInput} onLlmKeyChange={llm.setLlmKeyInput}
      onLlmFastChange={llm.setLlmFastInput} onLlmStrongChange={llm.setLlmStrongInput} onRestartRuntime={restart} onLlmProfileChange={llm.applyLlmProfile}
      onModelPicksChange={noop} onSaveLlmProfile={llm.saveLlmProfile} onClearLlmProfile={llm.clearLlmProfile} llmSaveNote={llm.llmSaveNote}
      hasResident residentName="vorto-97" residentConnected={false} onOpenResidentSetup={() => setNotice("常驻连接入口")} onOpenSchedule={() => setNotice("计划入口")}
      onEditAssistant={() => setNotice("资料入口")} onOpenPlugins={() => setNotice("插件入口")}
      onChooseRepo={noop} onDismissRecovery={noop} onRestoreRecovery={noop} onBaseUrlChange={noop} onTokenChange={noop} onStopRuntime={noop}
      onConnectExisting={noop} onStartRuntime={noop} onSwitchScope={noop} trust={trust} trustBusy={false} onTrustChange={level => setTrust({ ...trust, level, effective: level })} />}</>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
