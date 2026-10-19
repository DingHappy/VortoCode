// 设置页逐页截图用：模拟「已登录的中度会员、正在用会员模型」。不写配置、不连任何服务。
// 用法：qa/dist/qa/settings-member.html?section=account&theme=dark
import { mockIPC } from "@tauri-apps/api/mocks";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { SettingsModal, type SettingsSection } from "../src/components/SettingsModal";
import type { DesktopLlmProfileStatus, TrustStatus } from "../src/types";
import "../src/App.css";

const params = new URLSearchParams(location.search);
const section = (params.get("section") || "account") as SettingsSection;
document.documentElement.setAttribute("data-theme", params.get("theme") || "dark");
const day = 86400;
const now = Math.floor(Date.now() / 1000);
const guest = params.get("state") === "guest";  // 新用户：没登录、没配模型
const member: DesktopLlmProfileStatus = {
  configured: true, baseUrl: "https://relay.example.com/v1", model: "mimo-v2.6-pro", provider: "vortocode", requiresKey: true,
  contextWindow: 1048576, contextWindowSource: "service", models: ["mimo-v2.6-pro"],
  modelInfo: [{ id: "mimo-v2.6-pro", category: "omni", capabilities: ["vision", "audio_input", "tools", "reasoning"], tier: "coding", contextWindow: 1048576 }],
  account: { username: "vc_1", displayName: "me@example.com", keySource: "vortocode_member", planName: "中度", planExpiry: now + 30 * day },
  providers: [], configPath: "~/Library/Application Support/com.vorto.vortocode/llm-profile.json",
};
const profile: DesktopLlmProfileStatus = guest
  ? { configured: false, baseUrl: "", model: "", provider: "custom", requiresKey: true, providers: [] }
  : member;
mockIPC((cmd) => {
  switch (cmd) {
    case "account_status": if (guest) return { signedIn: false, email: "", deviceName: "", offline: false, manageUrl: "", note: "" };
      return { signedIn: true, email: "me@example.com", deviceName: "我的 MacBook Pro", offline: false, manageUrl: "https://account.example.com/account", note: "" };
    case "account_membership": if (guest) return { available: false, member: false, planName: "", expiresAt: 0, creditsAvailable: 0, creditsPerPeriod: 0, renewedUntil: 0, keyEnabled: false, inUse: false };
      return { available: true, member: true, planName: "中度", expiresAt: now + 30 * day, creditsAvailable: 6900, creditsPerPeriod: 6900, renewedUntil: now + 60 * day, keyEnabled: true, inUse: true };
    case "get_llm_profile": case "refresh_llm_providers": return profile;
    case "get_update_check_enabled": return true;
    case "get_experimental_workers": return false;
    case "get_llm_usage": return { available: false, unlimited: false, periodDays: 30, message: "会员额度见「账号」" };
    case "get_user_instructions": return { path: "~/.vortocode/AGENTS.md", content: "", exists: false };
    case "get_browser_control": return { enabled: false, browserPath: null };
    case "get_desktop_control": return { enabled: false, target: "local", apps: "" };
    default: return null;
  }
});
const noop = () => {};
function Fixture() {
  const [trust, setTrust] = useState<TrustStatus>({ level: "ask", effective: "ask", ceiling: "full", levels: ["ask", "reads", "full"], capability_profile: "local", workspace_scope: "project", local_file_reads: true, supervised: true });
  const [roles, setRoles] = useState<Record<string, string>>({});
  return <SettingsModal activeScope="general" connection="connected" connectionText="已连接" connectionNote="" runtimeStarting={false} projectSwitching={false}
    processStatus={{ running: true, message: "本机引擎运行中" }} recoveryRecord={null} llmProfile={profile}
    llmBaseInput={profile.baseUrl} llmModelInput={profile.model} llmKeyInput="" llmFastInput="" llmStrongInput="" llmRolesInput={roles} onLlmRolesChange={setRoles}
    llmProfileBusy={false} repoRoot="/Users/me/projects/demo" baseUrl="http://127.0.0.1:8080" token="" modelPicks={null} initialSection={section}
    onClose={noop} onLlmBaseChange={noop} onLlmModelChange={noop} onLlmKeyChange={noop} onLlmFastChange={noop} onLlmStrongChange={noop}
    onRestartRuntime={async () => false} onLlmProfileChange={noop} onModelPicksChange={noop} onSaveLlmProfile={noop} onClearLlmProfile={noop}
    onChooseRepo={noop} onDismissRecovery={noop} onRestoreRecovery={noop} onBaseUrlChange={noop} onTokenChange={noop} onStopRuntime={noop}
    onConnectExisting={noop} onStartRuntime={noop} onSwitchScope={noop} trust={trust} trustBusy={false} onTrustChange={(level) => setTrust({ ...trust, level, effective: level })} />;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
