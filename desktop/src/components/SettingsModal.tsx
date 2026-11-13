// Keep real settings endpoints and native confirmation flows; page navigation retains unsaved forms.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { SETTINGS_OWNER_LABELS, SETTINGS_SECTIONS, searchSettings, settingsPage, type SettingsPage, type SettingsSection } from "../lib/settingsNavigation";
import "./SettingsModal.css";
import { SettingsTargetSection } from "./settings/SettingsTargetSection";
import { settingsTargetSummary, type SettingsTargetLease } from "../lib/settingsTarget";
export type { SettingsSection } from "../lib/settingsNavigation";
import { invoke } from "@tauri-apps/api/core";
import { errorText } from "../lib/errorText";
import type { CodexObserverRead } from "../lib/codexObserverHealth";
import type { ReactNode } from "react";
import { UserRound, Cpu, Monitor, ShieldCheck, Blocks, SlidersHorizontal, Info, Search, X, MapPin, ChevronRight, Check, CircleAlert, Circle, LoaderCircle } from "lucide-react";
import { Eye, EyeOff } from "lucide-react";
import { BuildVersion } from "./BuildVersion";
import { openUrl } from "@tauri-apps/plugin-opener";
import { loadTelemetrySettings, PRIVACY_URL, saveTelemetrySettings, type TelemetrySettings } from "../lib/telemetry";
import { restartSavedSetting } from "../lib/settingsApply";
import type { ModelSaveKind } from "../lib/modelProfileSave";
import { applyModelPreset, modelSettingsState } from "../lib/modelSettings";

import { contextWindowSourceLabel, formatTokenCount } from "../lib/labels";
import { isLocalModelBase, PROVIDER_PRESETS } from "../lib/providers";
import type { StaffClient } from "../lib/staff";
import { StaffPane } from "./settings/StaffPane";
import { ConfigPane } from "./settings/ConfigPane";
import { LlmConnectionCheck } from "./settings/LlmConnectionCheck";
import { AccountPane } from "./settings/AccountPane";
import { BrowserPane } from "./settings/BrowserPane";
import { UpdateCard } from "./settings/UpdateCard";
import { VortoAccountCard } from "./settings/VortoAccountCard";
import { ResidentAccountCard } from "./settings/ResidentAccountCard";
import { LocalResidentCard } from "./settings/LocalResidentCard";
import type { LocalResidentStatus } from "../lib/localResident";
import { MEMBER_KEY_SOURCE } from "../lib/vortoAccount";
import { DesktopControlPane } from "./settings/DesktopControlPane";
import { ResidentDesktopPane } from "./ResidentDesktopPane";
import { ModelProfileConflict } from "./settings/ModelProfileConflict";
import { ModelConfigurationState } from "./settings/ModelConfigurationState";
import { ModelServices } from "./settings/ModelServices";
import { CodexWatchCard } from "./settings/CodexWatchCard";
import { ModelPicks } from "./settings/ModelPicks";
import { ModelRoles } from "./settings/ModelRoles";
import { PersonalizePane } from "./settings/PersonalizePane";
import { UsagePane } from "./settings/UsagePane";
import {
  THEME_OPTIONS,
  applyThemePreference,
  readThemePreference,
  saveThemePreference,
  type ThemePreference,
} from "../lib/theme";
import { trustCard } from "../lib/trustCard";
import type {
  ConnectionState,
  DesktopLlmProfileStatus,
  DesktopProjectProfile,
  GatewayProcessStatus,
  GatewayRecoveryRecord,
  EngineWorkersView,
  TrustLevel,
  TrustStatus,
  WorkspaceScope,
} from "../types";

export function relayMode(profile: { provider?: string; account?: unknown } | null | undefined): boolean {
  return profile?.provider === "vortocode" || Boolean(profile?.account);
}

/** 「账号与会员」里两块按需出现：旧的中转站用户名密码登录（只给真用它登录过的人，会员 Key 不算）；
 *  「使用情况」跟着中转站走（会员也在用中转站）。 */
export function settingsBlockVisible(
  block: "relayLogin" | "usage",
  profile: { provider?: string; account?: { keySource?: string } | null } | null | undefined,
): boolean {
  if (block === "relayLogin") return Boolean(profile?.account) && profile?.account?.keySource !== MEMBER_KEY_SOURCE;
  return relayMode(profile);
}

const NAV_ICONS = { account: UserRound, model: Cpu, computer: Monitor, permissions: ShieldCheck, staff: UserRound, tools: Blocks, personal: SlidersHorizontal, advanced: Info };
type DraftState = { dirty: boolean; busy: boolean; conflict?: boolean };
const DRAFT_ITEMS: Record<string, { label: string; page: SettingsPage; anchor: string }> = {
  provider: { label: "模型服务", page: "model", anchor: "provider-editor" },
  instructions: { label: "全局指令", page: "personal", anchor: "instructions" },
  desktop: { label: "远程桌面", page: "computer", anchor: "desktop" },
  control: { label: "桌面操作", page: "permissions", anchor: "control" },
  browser: { label: "浏览器操作", page: "permissions", anchor: "browser" },
};

type SettingsModalProps = {
  /** 打开插件页（MCP 服务与目录） */
  onOpenPlugins?: () => void;
  /** 登记了常驻电脑（「让助理查看 Codex」要报给它） */
  hasResident?: boolean;
  staffClient?: StaffClient;
  runtimeSettings?: SettingsTargetLease<StaffClient>;
  readCodexObserver?: () => Promise<CodexObserverRead>;
  /** 打开助理资料（名字、SOUL、关于你） */
  onEditAssistant?: () => void;
  residentName?: string;
  residentAccountProjectId?: string;
  residentConnected?: boolean;
  residentRegistrationKnown?: boolean;
  selectedAssistantHostId?: string;
  localResidentProjectId?: string;
  remoteResidentProjectId?: string;
  onUseLocalResident?: (profile: DesktopProjectProfile) => Promise<void>;
  onLocalResidentStatus?: (status: LocalResidentStatus) => void;
  onOpenResidentSetup?: () => void;
  onConnectResident?: () => void;
  onOpenSchedule?: () => void;
  llmSaveNote?: string;
  llmSaveKind?: ModelSaveKind;
  llmProfileLoading?: boolean;
  llmProfileError?: string;
  llmProfileConflict?: boolean;
  onReloadLlmProfile?: () => void;
  activeScope: WorkspaceScope;
  connection: ConnectionState;
  connectionText: string;
  connectionNote: string;
  runtimeStarting: boolean;
  runtimeActionBusy?: boolean;
  runtimeActionNote?: string;
  runtimeIsRemote?: boolean;
  onReloadRuntime?: () => void;
  projectSwitching: boolean;
  processStatus: GatewayProcessStatus;
  recoveryRecord: GatewayRecoveryRecord | null;
  llmProfile: DesktopLlmProfileStatus | null;
  llmBaseInput: string;
  llmModelInput: string;
  llmKeyInput: string;
  llmFastInput: string;
  llmStrongInput: string;
  llmRolesInput: Record<string, string>;
  onLlmRolesChange: (value: Record<string, string>) => void;
  engineWorkers?: EngineWorkersView | null;
  llmProfileBusy: boolean;
  repoRoot: string;
  baseUrl: string;
  token: string;
  children?: ReactNode;
  onClose: () => void;
  onLlmBaseChange: (value: string) => void;
  onLlmModelChange: (value: string) => void;
  onLlmFastChange: (value: string) => void;
  onLlmStrongChange: (value: string) => void;
  onRestartRuntime: () => Promise<boolean>;
  onLlmProfileChange: (profile: DesktopLlmProfileStatus) => void;
  /** 输入框里的常用模型短名单；null = 跟随服务端推荐。 */
  modelPicks: string[] | null;
  onModelPicksChange: (picks: string[] | null) => void;
  initialSection?: SettingsSection;
  /** 打开时滚到并高亮的条目（settingsNavigation 的条目 id） */
  initialAnchor?: string;
  onLlmKeyChange: (value: string) => void;
  onSaveLlmProfile: () => void;
  onClearLlmProfile: () => void;
  onChooseRepo: () => void;
  onDismissRecovery: () => void;
  onRestoreRecovery: () => void;
  onBaseUrlChange: (value: string) => void;
  onTokenChange: (value: string) => void;
  onStopRuntime: () => void;
  onConnectExisting: () => void;
  onStartRuntime: () => void;
  onSwitchScope: (scope: "general" | "scratch") => void;
  trust: TrustStatus | null;
  trustBusy: boolean;
  onTrustChange: (level: TrustLevel) => void;
};

export function SettingsModal({
  onOpenPlugins,
  hasResident = false,
  readCodexObserver, staffClient, runtimeSettings,
  onEditAssistant,
  residentName, residentAccountProjectId, residentConnected = false, onOpenResidentSetup, onConnectResident, onOpenSchedule, llmSaveNote = "", llmSaveKind = "idle", llmProfileLoading = false, llmProfileError = "", llmProfileConflict = false, onReloadLlmProfile,
  selectedAssistantHostId, localResidentProjectId, remoteResidentProjectId, onUseLocalResident, onLocalResidentStatus, residentRegistrationKnown = true,
  activeScope,
  connection,
  connectionText,
  connectionNote,
  runtimeStarting, runtimeActionBusy = false, runtimeActionNote = "", runtimeIsRemote = false, onReloadRuntime,
  projectSwitching,
  processStatus,
  recoveryRecord,
  llmProfile,
  llmBaseInput,
  llmModelInput,
  llmKeyInput,
  llmFastInput,
  llmStrongInput,
  llmRolesInput,
  onLlmRolesChange,
  engineWorkers = null,
  llmProfileBusy,
  repoRoot,
  baseUrl,
  token,
  children,
  onClose,
  onLlmBaseChange,
  onLlmModelChange,
  onLlmFastChange,
  onLlmStrongChange,
  onRestartRuntime: restartRuntime,
  onLlmProfileChange,
  modelPicks,
  onModelPicksChange,
  initialSection,
  initialAnchor,
  onLlmKeyChange,
  onSaveLlmProfile,
  onClearLlmProfile,
  onChooseRepo,
  onDismissRecovery,
  onRestoreRecovery,
  onBaseUrlChange,
  onTokenChange,
  onStopRuntime,
  onConnectExisting,
  onStartRuntime,
  onSwitchScope,
  trust,
  trustBusy,
  onTrustChange,
}: SettingsModalProps) {
  // Local writes stay local; reloading an engine must not follow a changed conversation target.
  const onRestartRuntime = () => runtimeSettings?.current() ? restartRuntime() : Promise.resolve(false);
  const onRuntimeAction = (action: () => void) => { if (runtimeSettings?.current()) action(); };
  const boundResidentId = runtimeSettings?.target.space?.runtime.registrationId ?? undefined;
  const trustView = trustCard(trust);
  const llmInputIsLocal = isLocalModelBase(llmBaseInput);
  const [section, setSection] = useState<SettingsPage>(settingsPage(
    initialSection ?? (llmProfile && !llmProfile.configured ? "model" : "account")));
  const [visited, setVisited] = useState<Set<SettingsPage>>(() => new Set([section]));
  const [highlight, setHighlight] = useState(initialAnchor ?? "");
  const dialog = useDialogSurface();
  const contentRef = useRef<HTMLDivElement>(null);
  const [closeAction, setCloseAction] = useState<(() => void) | null>(null);
  const [drafts, setDrafts] = useState<Record<string, DraftState>>({});
  const [localResidentBusy, setLocalResidentBusy] = useState(false);
  const [localResidentStatus, setLocalResidentStatus] = useState<LocalResidentStatus | null>(null);
  const draftCallbacks = useMemo(() => Object.fromEntries(Object.keys(DRAFT_ITEMS).map(key => [key,
    (state: DraftState) => setDrafts(previous => ({ ...previous, [key]: state })),
  ])), []);
  const navigate = (next: SettingsPage, anchor = "") => {
    setVisited(previous => new Set([...previous, next]));
    setSection(next); setHighlight(anchor); setQuery("");
    contentRef.current?.scrollTo({ top: 0 });
  };
  useEffect(() => {
    if (!highlight) return;
    const node = document.getElementById(`setting-${highlight}`);
    node?.scrollIntoView({ block: "start", behavior: "auto" });
    node?.focus({ preventScroll: true });
  }, [section, highlight]);
  const [query, setQuery] = useState("");
  const [themePreference, setThemePreference] = useState<ThemePreference>(readThemePreference);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [showKey, setShowKey] = useState(false);
  // 实验功能：外部执行者（Desktop 偏好，默认关闭）。打开要过原生确认，开关后重启当前引擎才生效。
  const [experimental, setExperimental] = useState(false);
  const [experimentalBusy, setExperimentalBusy] = useState(false);
  const [experimentalNote, setExperimentalNote] = useState("");
  useEffect(() => {
    let alive = true;
    invoke<boolean>("get_experimental_workers").then((value) => { if (alive) setExperimental(Boolean(value)); }).catch(() => { });
    return () => { alive = false; };
  }, []);
  const toggleExperimental = async (next: boolean) => {
    setExperimentalBusy(true);
    setExperimentalNote("");
    try {
      setExperimental(await invoke<boolean>("set_experimental_workers", { enabled: next }));
      setExperimentalNote((await restartSavedSetting(onRestartRuntime, next ? "实验执行者配置已开启" : "实验执行者配置已关闭")).message);
    } catch (failure) {
      setExperimentalNote(errorText(failure, "切换实验功能失败"));
    } finally {
      setExperimentalBusy(false);
    }
  };
  const draft = { baseUrl: llmBaseInput, model: llmModelInput, apiKey: llmKeyInput, fastModel: llmFastInput, strongModel: llmStrongInput, roleModels: llmRolesInput };
  // 默认服务的详细表单：点「编辑」，或还没有服务时在「添加模型服务」里选了预设，才展开
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const [editingDefault, setEditingDefault] = useState(false);
  const modelState = modelSettingsState(llmProfile, draft);
  const choosePreset = (preset: typeof PROVIDER_PRESETS[number]) => {
    const next = applyModelPreset(draft, preset);
    if (next === draft) return;
    onLlmBaseChange(next.baseUrl); onLlmModelChange(next.model); onLlmKeyChange(next.apiKey);
    setShowKey(false); setModelOptions([]);
  };
  const searchResults = useMemo(() => searchSettings(query), [query]);
  const currentPage = SETTINGS_SECTIONS.find(item => item.id === section)!;
  const modelDirty = Boolean(llmProfile && (llmProfile.configured ? modelState.dirty
    : editingDefault && (llmBaseInput.trim() || llmModelInput.trim() || llmKeyInput.trim())));
  const pendingChanges = [
    ...(modelDirty ? [{ label: "默认模型与分工", page: "model" as SettingsPage, anchor: editingDefault ? "services" : "roles" }] : []),
    ...Object.entries(drafts).filter(([, state]) => state.dirty).map(([key]) => DRAFT_ITEMS[key]),
  ];
  const pending = pendingChanges.map(item => item.label);
  const controlScope = processStatus.scope ?? activeScope;
  const controlRoot = processStatus.repoRoot || repoRoot;
  const modelUnavailable = llmProfileLoading || Boolean(llmProfileError);
  const modelConflict = llmProfileConflict || Boolean(drafts.provider?.conflict);
  const modelReadMessage = llmProfileLoading ? "正在读取本机模型配置…" : llmProfileError;
  const modelFeedback = section === "model" ? modelReadMessage || (llmProfileConflict ? "已保存配置有更新，请先核对最新配置" : llmSaveNote) : "";
  const modelFeedbackError = section === "model" && (Boolean(llmProfileError) || llmProfileConflict || llmSaveKind === "error");
  const otherSaving = runtimeActionBusy || recoveryBusy || llmProfileBusy || experimentalBusy || trustBusy || Object.values(drafts).some(state => state.busy);
  const saving = localResidentBusy || otherSaving;
  const knownLocalId = localResidentProjectId ?? localResidentStatus?.projectId;
  const knownRemoteId = remoteResidentProjectId ?? residentAccountProjectId;
  const remoteIsLocal = Boolean(knownLocalId && knownRemoteId === knownLocalId);
  const currentUsesLocalResident = Boolean(knownLocalId && selectedAssistantHostId === knownLocalId);
  const hasRemoteResident = residentRegistrationKnown && hasResident && !remoteIsLocal;
  const remoteResidentConnected = residentRegistrationKnown && residentConnected && !remoteIsLocal && !currentUsesLocalResident;
  const requestLeave = (action: () => void) => {
    if (saving) return;
    if (pending.length) setCloseAction(() => action);
    else action();
  };
  const resetModelDraft = useCallback(() => {
    if (!llmProfile) return;
    onLlmBaseChange(llmProfile.baseUrl); onLlmModelChange(llmProfile.model); onLlmKeyChange("");
    onLlmFastChange(llmProfile.fastModel ?? ""); onLlmStrongChange(llmProfile.strongModel ?? "");
    onLlmRolesChange(llmProfile.roleModels ?? {});
  }, [llmProfile, onLlmBaseChange, onLlmModelChange, onLlmKeyChange, onLlmFastChange, onLlmStrongChange, onLlmRolesChange]);
  const anchorClass = (id: string) => `settings-anchor${highlight === id ? " settings-highlight" : ""}`;

  const changeTheme = (preference: ThemePreference) => {
    setThemePreference(preference);
    saveThemePreference(preference);
    applyThemePreference(preference);
  };

  const content = (
    <dialog ref={dialog} className="settings-modal settings-layout settings-redesign" aria-label="设置"
      onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); if (event.nativeEvent.isComposing) event.preventDefault(); } }}
      onCancel={event => { event.preventDefault(); requestLeave(onClose); }}>
      <nav className="settings-nav" aria-label="设置分类">
        <div className="settings-brand"><SlidersHorizontal size={18} /><h2>设置</h2><span>VortoOne</span></div>
        <div className="settings-search-wrap"><Search size={15} aria-hidden="true" /><input type="search" className="settings-search" value={query}
          onChange={event => setQuery(event.target.value)} placeholder="搜索设置项" aria-label="搜索设置" /></div>
        <div className="settings-nav-group">
          {query.trim() ? searchResults.map(item => {
            const Icon = NAV_ICONS[item.page];
            return <button key={item.id} className="settings-search-result" onClick={() => navigate(item.page, item.id)}>
              <Icon size={17} aria-hidden="true" /><span>{item.label}<small>{SETTINGS_SECTIONS.find(page => page.id === item.page)?.label} · {SETTINGS_OWNER_LABELS[item.owner]}</small></span><ChevronRight size={14} aria-hidden="true" />
            </button>;
          }) : SETTINGS_SECTIONS.map(item => {
            const Icon = NAV_ICONS[item.id];
            return <button key={item.id} className={section === item.id ? "active" : ""} aria-current={section === item.id ? "page" : undefined}
              aria-describedby={pendingChanges.some(change => change.page === item.id) ? `settings-pending-${item.id}` : undefined}
              onClick={() => navigate(item.id)}><Icon size={17} aria-hidden="true" /><span>{item.label}</span>
              {pendingChanges.some(change => change.page === item.id) && <><Circle className="settings-pending-dot" size={6} fill="currentColor" aria-hidden="true" /><span id={`settings-pending-${item.id}`} hidden>有未保存的修改</span></>}
            </button>;
          })}
          {query.trim() && !searchResults.length && <p className="settings-nav-empty">没有匹配的设置项<br />试试「远程」「模型」或「权限」</p>}
        </div>
        <div className="settings-nav-footer"><Monitor size={15} /><div>按区块核对保存位置<small>本机、空间和项目分别管理</small></div></div>
      </nav>
      <div className="settings-pane">
        <header className="settings-heading"><div><h2 id="settings-page-title">{currentPage.label}</h2><p>{currentPage.description}</p></div>
          <button className="settings-close" disabled={saving} onClick={() => requestLeave(onClose)} aria-label="关闭设置"><X size={18} /></button></header>
        <div className="settings-content" ref={contentRef} aria-labelledby="settings-page-title">
          <div className="settings-target-current"><strong>打开设置时的运行目标</strong><span>{runtimeSettings ? settingsTargetSummary(runtimeSettings.target) : "目标尚未核对"}</span>
            {runtimeSettings && !runtimeSettings.current() && <small role="status">当前目标已失效或尚未连接；运行端设置已停用。关闭后重开以核对新目标。</small>}</div>
          <div className="settings-scope"><MapPin size={16} aria-hidden="true" /><p>{currentPage.scope}</p></div>
          {visited.has("account") && <div hidden={section !== "account"} className="settings-page">
            <div id="setting-account" className={anchorClass("account")} tabIndex={-1}>
              <SettingsTargetSection owner="desktop"><VortoAccountCard
                modelConfigured={Boolean(llmProfile?.configured)}
                onProfile={onLlmProfileChange}
                onRestartRuntime={onRestartRuntime}
                onProfileCleared={async () => onLlmProfileChange(await invoke<DesktopLlmProfileStatus>("get_llm_profile"))}
              />
              {settingsBlockVisible("relayLogin", llmProfile) && (
                <AccountPane
                  profile={llmProfile}
                  onProfile={onLlmProfileChange}
                  onRestartRuntime={onRestartRuntime}
                  onOpenModelSettings={() => navigate("model")}
                />
              )}
              {settingsBlockVisible("usage", llmProfile) && <UsagePane account={llmProfile?.account} />}
              </SettingsTargetSection>
              <SettingsTargetSection owner="space" lease={runtimeSettings} unavailable={!boundResidentId}>
                <div id="setting-mobile-account" className={anchorClass("mobile-account")} tabIndex={-1}>
                  <ResidentAccountCard key={boundResidentId ?? "no-resident"} projectId={boundResidentId} guard={runtimeSettings} />
                </div>
              </SettingsTargetSection>
            </div>
          </div>}
          {visited.has("model") && <div hidden={section !== "model"} className="settings-page" aria-busy={llmProfileLoading}><SettingsTargetSection owner="desktop">
            {modelUnavailable && <section className="settings-read-feedback" role={llmProfileError ? "alert" : "status"}>
              {llmProfileLoading ? <LoaderCircle size={18} aria-hidden="true" /> : <CircleAlert size={18} aria-hidden="true" />}
              <div><strong>{llmProfileLoading ? "正在读取本机模型配置…" : "暂时无法读取本机模型配置"}</strong>
                <p>{llmProfileLoading ? "读取完成后即可编辑。其他分类仍可查看。" : `${llmProfileError}。现有配置不会被清空；请重试后再编辑。`}</p></div>
              {!llmProfileLoading && onReloadLlmProfile && <button onClick={onReloadLlmProfile}>重试读取</button>}
            </section>}
            {llmProfileConflict && <ModelProfileConflict onUseLatest={onLlmProfileChange} onBusyChange={setRecoveryBusy} />}
            {!modelUnavailable && !modelConflict && <ModelConfigurationState active={section === "model"} revision={llmProfile?.revision} />}
            {modelConflict && <p className="settings-card-note" role="status">模型配置有冲突，引擎生效状态待核对。载入最新配置后再重新核对。</p>}
            {(!modelUnavailable || llmProfile) && <fieldset className="settings-model-fields" disabled={saving || modelUnavailable}>
              <div id="setting-services" className={anchorClass("services")} tabIndex={-1}><fieldset className="settings-model-fields" disabled={modelDirty || saving}><ModelServices onDraftStateChange={draftCallbacks.provider} profile={llmProfile} onProfile={onLlmProfileChange} onRestartRuntime={onRestartRuntime}
                editingDefault={editingDefault} onEditDefault={setEditingDefault}
                onSetupDefault={(preset) => {
                  if (preset) choosePreset(preset);
                  else { onLlmBaseChange(""); onLlmModelChange(""); onLlmKeyChange(""); setModelOptions([]); }
                  setEditingDefault(true);
                }}
                onOpenAccount={() => navigate("account")} /></fieldset>{modelDirty && <p className="settings-card-note">保存或还原默认模型草稿后，可继续管理其他模型服务。</p>}</div>
              {editingDefault && (
                <section className={`llm-profile-card ${llmProfile?.configured ? "configured" : "unconfigured"}`} aria-label="默认服务设置">
                  <div className="llm-profile-head">
                    <div>
                      <span>{llmProfile?.configured ? "编辑默认服务" : "设置默认服务"}</span>
                      <strong>{llmProfile?.configured ? "改地址、模型或 Key" : "填写地址 → 获取模型列表 → 选模型 → 保存"}</strong>
                    </div>
                  </div>
                  <div className="llm-profile-fields">
                    <label>
                      <span>模型服务地址</span>
                      <input value={llmBaseInput} onChange={(event) => onLlmBaseChange(event.target.value)} placeholder="https://your-gateway.example/v1" />
                    </label>
                    <label>
                      <span>模型名</span>
                      <input
                        value={llmModelInput}
                        onChange={(event) => onLlmModelChange(event.target.value)}
                        placeholder="服务中的模型名；可先获取模型列表"
                        list="llm-model-options"
                      />
                      <datalist id="llm-model-options">
                        {modelOptions.map((model) => <option key={model} value={model} />)}
                      </datalist>
                    </label>
                    <label>
                      <span>API Key <em>{modelState.sameService ? "留空沿用此服务已保存的 Key" : llmInputIsLocal ? "仅无需鉴权时可留空；否则填写本机服务 Key" : "保存在本机配置文件，仅你的账户可读"}</em></span>
                      <div className="llm-key-field"><input
                        type={showKey ? "text" : "password"}
                        value={llmKeyInput}
                        onChange={(event) => onLlmKeyChange(event.target.value)}
                        autoComplete="new-password"
                        placeholder={modelState.sameService ? "留空保留原 Key；替换时输入新 Key" : "输入此服务的 Key"}
                      /><button type="button" aria-label={showKey ? "隐藏 API Key" : "显示 API Key"} onClick={() => setShowKey(!showKey)}>{showKey ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>
                    </label>
                  </div>
                  <div className={`llm-model-capability ${modelState.sameModel && (llmProfile?.contextWindow ?? 0) > 0 ? "known" : "unknown"}`}>
                    <span>模型上下文</span>
                    <strong>{modelState.sameModel && (llmProfile?.contextWindow ?? 0) > 0 ? `${formatTokenCount(llmProfile?.contextWindow)} tokens` : "当前草稿尚未检测"}</strong>
                    <em>{modelState.sameModel ? contextWindowSourceLabel(llmProfile?.contextWindowSource) : "旧配置的信息不适用于此草稿"}</em>
                  </div>
                  <p className="llm-profile-note">线上服务要用 https；本机服务可以用 http://127.0.0.1 或 localhost。保存到本机后尝试重启本机引擎；未运行或重启失败时，下次启动生效。</p>
                  <LlmConnectionCheck baseUrl={llmBaseInput} apiKey={llmKeyInput} model={llmModelInput} onModels={setModelOptions} />
                  <p className="llm-save-status" role="status">{modelState.missing || (modelState.dirty ? "修改尚未保存，不会影响当前对话。" : "和已保存的一致。")}</p>
                  <div className="llm-profile-actions">
                    {llmProfile?.configured && <button onClick={() => void onClearLlmProfile()} disabled={llmProfileBusy}>清除配置</button>}
                  </div>
                </section>
              )}
              {llmProfile?.configured && (<>
                <section id="setting-roles" className={`${anchorClass("roles")} settings-card`} tabIndex={-1}><div className="settings-card-head"><strong>模型与执行者分工</strong><span className="settings-scope-tag">这台 Mac</span></div>
                  <ModelRoles profile={llmProfile} value={llmRolesInput} onChange={onLlmRolesChange} disabled={llmProfileBusy} engine={engineWorkers}
                    experimental={experimental} experimentalBusy={experimentalBusy} experimentalNote={experimentalNote} onExperimentalToggle={(next) => void toggleExperimental(next)} />
                </section>
                <details id="setting-routing" className={`${anchorClass("routing")} llm-extra-settings model-advanced`} open={highlight === "routing" || undefined}><summary>高级：自动调度的快速 / 强力模型、常用模型名单</summary>
                  <div className="llm-routing-fields">
                    <p>智能调度 <em>输入框选「自动」时，简短问答走快速模型，Build、带图或复杂任务走强力模型；留空则都用主模型。</em></p>
                    <label>
                      <span>快速模型</span>
                      <input value={llmFastInput} onChange={(event) => onLlmFastChange(event.target.value)} placeholder="可选，例如响应更快的小模型" list="llm-model-options" />
                    </label>
                    <label>
                      <span>强力模型</span>
                      <input value={llmStrongInput} onChange={(event) => onLlmStrongChange(event.target.value)} placeholder="可选，例如推理更强的大模型" list="llm-model-options" />
                    </label>
                  </div>
                  <p className="llm-profile-note">快速与强力模型随底部「保存模型配置」一起应用；下方常用模型名单独生效。</p>
                  <ModelPicks profile={llmProfile} picks={modelPicks} onChange={onModelPicksChange} />
                </details>
              </>)}
              {!llmProfile?.configured && <section id="setting-roles" className={`${anchorClass("roles")} settings-card`} tabIndex={-1}>
                <strong>模型与执行者分工</strong><p className="settings-card-note">先添加默认模型服务，再配置研究、实现与审查的分工。</p>
                <div id="setting-routing" className={anchorClass("routing")} tabIndex={-1}><p className="settings-card-note">配置服务后可设置自动调度与常用模型。</p></div>
              </section>}
            </fieldset>}
          </SettingsTargetSection></div>}
          {visited.has("computer") && <div hidden={section !== "computer"} className="settings-page"><SettingsTargetSection owner="desktop">
            <div id="setting-local-resident" className={anchorClass("local-resident")} tabIndex={-1}><LocalResidentCard
              onUse={onUseLocalResident ? async profile => { if (pending.length || otherSaving) throw new Error("请先处理未保存的设置，再连接本机常驻助理"); await onUseLocalResident(profile); } : undefined}
              selectedHostId={selectedAssistantHostId} connected={residentConnected} onBusyChange={setLocalResidentBusy}
              blocked={Boolean(pending.length || otherSaving || projectSwitching)}
              onStatus={status => { setLocalResidentStatus(status); onLocalResidentStatus?.(status); }} /></div>
            <section id="setting-resident" className={`${anchorClass("resident")} settings-card`} tabIndex={-1} aria-label="常驻助手连接">
              <div className="settings-card-head"><strong>其他电脑上的常驻助理</strong><span className="settings-scope-tag">独立实例</span></div>
              <div className="settings-connection-row"><div><strong>{!residentRegistrationKnown ? "正在核对已登记的常驻电脑" : hasRemoteResident ? residentName || "已登记常驻电脑" : "尚未登记其他常驻电脑"}</strong>
                <p>{!residentRegistrationKnown ? "登记状态尚未核对；可先添加其他电脑的连接。" : remoteResidentConnected ? "当前对话已连接到常驻实例" : hasRemoteResident ? "已登记；当前对话未连接到该实例" : "连接专用电脑上的助手服务，让它在远端持续运行。"}</p></div>
                {(hasRemoteResident || !residentRegistrationKnown) && onConnectResident && !remoteResidentConnected && <button disabled={saving || !residentRegistrationKnown} onClick={() => requestLeave(() => { onClose(); onConnectResident(); })}>连接助手</button>}
                {onOpenResidentSetup && <button disabled={saving} onClick={() => requestLeave(() => { onClose(); onOpenResidentSetup(); })}>{hasRemoteResident ? "添加常驻连接" : "连接常驻电脑"}</button>}</div>
              <p className="settings-card-note">助手服务的连接不代表远程画面已连接，也不会自动启用桌面操作。</p>
            </section>
            <div id="setting-desktop" className={anchorClass("desktop")} tabIndex={-1}><ResidentDesktopPane onDraftStateChange={draftCallbacks.desktop} /></div>
            <section id="setting-runtime" className={`${anchorClass("runtime")} settings-card`} tabIndex={-1}><div className="settings-card-head"><strong>本机引擎与工作区</strong><span className="settings-scope-tag">这台 Mac</span></div>
              <p className="settings-card-note">临时引擎退出 Desktop 后停止；不开定时任务。</p>
              <p className="settings-runtime-status">{runtimeIsRemote ? currentUsesLocalResident ? "当前对话使用本机常驻助理；下方仅管理临时引擎" : "当前会话连接到远端实例" : runtimeStarting ? "正在准备本机引擎…" : processStatus.running ? "本机引擎运行中" : "本机引擎未运行"}</p>
              <p className="settings-card-note">当前目标：{runtimeIsRemote ? currentUsesLocalResident ? "本机常驻会话；此处不控制常驻服务" : "远端会话；此处不控制服务器" : controlScope === "general" ? "本机通用工作区" : controlScope === "scratch" ? "本机隔离工作区" : `本机项目 · ${controlRoot || "尚未选择目录"}`}</p>
              <div className="settings-actions" aria-label="本机引擎控制">
                {runtimeIsRemote ? <button disabled={saving || runtimeStarting || projectSwitching} onClick={() => requestLeave(() => { onClose(); onSwitchScope("general"); })}>切到本机通用工作区</button>
                  : <>
                    {!processStatus.running && <button className="primary" disabled={saving || runtimeStarting || projectSwitching || controlScope === "project" && !controlRoot.trim()} onClick={() => void onStartRuntime()}>{runtimeStarting ? "正在准备…" : "启动本机引擎"}</button>}
                    {processStatus.running && <>
                      {connection !== "connected" && <button disabled={saving || runtimeStarting || projectSwitching} onClick={() => void onStartRuntime()}>重新连接本机引擎</button>}
                      <button className="primary" disabled={saving || runtimeStarting || projectSwitching || !processStatus.launchId || !onReloadRuntime} onClick={() => onReloadRuntime?.()}>重新加载本机引擎</button>
                      <button className="danger" disabled={saving || runtimeStarting || projectSwitching || !processStatus.launchId} onClick={() => void onStopRuntime()}>停止本机引擎</button>
                    </>}
                  </>}
              </div>
              <p className="settings-card-note" role="status">{runtimeIsRemote ? currentUsesLocalResident ? "切到本机工作区后可管理临时引擎；常驻任务继续运行。" : "切到本机工作区后可管理本机引擎；远端任务继续运行。" : runtimeActionBusy ? "正在核对引擎活动，请等待操作完成…" : runtimeActionNote || "停止与重新加载前会即时核对会话、后台任务和终端；仍有活动或无法确认时保留引擎。"}</p>
              {!runtimeIsRemote && <details className="settings-runtime-details"><summary>工作区与连接诊断</summary>
                <>
                  <label>
                    <span>{activeScope === "project" ? "Git 项目" : "可选项目"}</span>
                    <div className="field-row">
                      <input value={repoRoot} readOnly placeholder="尚未选择项目" />
                      <button onClick={() => void onChooseRepo()} disabled={saving || projectSwitching || runtimeStarting}>选择…</button>
                    </div>
                  </label>

                  <div className={`process-card ${processStatus.running ? "running" : ""}`}>
                    <span className="process-indicator" />
                    <div>
                      <strong>{runtimeStarting ? "正在准备本地引擎" : processStatus.running ? "本机引擎运行中" : "本机引擎未运行"}</strong>
                      <p>{runtimeStarting ? "正在启动内置引擎并恢复会话…" : processStatus.message || "在这台 Mac 启动的工作区会在这里显示状态。"}</p>
                    </div>
                  </div>

                  <p className="settings-card-note">当前会话：{connectionText}{connectionNote ? ` · ${connectionNote}` : ""}</p>
                  {activeScope === "project" && !processStatus.running && connection !== "connected" && recoveryRecord && recoveryRecord.scope !== "general" && (
                    <div className={`recovery-card ${recoveryRecord.status}`}>
                      <div className="recovery-heading">
                        <strong>{recoveryRecord.status === "crashed" ? "runtime 异常退出" : "检测到未完成的 runtime 会话"}</strong>
                        <time>{new Date(recoveryRecord.updatedAt * 1_000).toLocaleString("zh-CN")}</time>
                      </div>
                      <p>{recoveryRecord.status === "crashed"
                        ? "上次工作区意外停止，VortoOne 可以重新启动并恢复项目配置。"
                        : "检测到上次没有完成退出清理。VortoOne 会先尝试恢复，失败后启动新的本地引擎。"}</p>
                      <code title={recoveryRecord.repoRoot}>{recoveryRecord.repoRoot}</code>
                      <div className="recovery-actions">
                        <button disabled={saving} onClick={() => void onDismissRecovery()}>忽略记录</button>
                        <button className="primary" disabled={saving} onClick={() => void onRestoreRecovery()}>恢复工作区</button>
                      </div>
                    </div>
                  )}

                </>

                <details className="advanced-settings">
                  <summary>高级连接设置</summary>
                  <p>仅在连接手动启动的 `vc server` 或排查本地引擎时使用。</p>
                  <label>
                    <span>Gateway 地址</span>
                    <input disabled={saving} value={baseUrl} onChange={(event) => onBaseUrlChange(event.target.value)} placeholder="http://127.0.0.1:8080" />
                  </label>
                  <label>
                    <span>API Token <em>仅保存在当前进程内</em></span>
                    <input disabled={saving} type="password" value={token} onChange={(event) => onTokenChange(event.target.value)} placeholder="本机无 token 时留空" />
                  </label>
                  <div className="settings-note">
                    只允许 127.0.0.1 / localhost。{processStatus.running && processStatus.command ? `当前引擎：${processStatus.command}` : "此入口只连接本机；常驻电脑使用上方的助手连接。"}
                  </div>
                  <div className="advanced-actions">
                    <button onClick={() => void onConnectExisting()} disabled={saving || !repoRoot.trim() || runtimeStarting}>连接已有实例</button>
                  </div>
                </details>

                <div className="settings-actions">
                  {activeScope === "project" && repoRoot.trim() ? (
                    connection === "connected"
                      ? <button className="primary" disabled={saving} onClick={() => requestLeave(onClose)}>完成</button>
                      : <button className="primary" onClick={() => void onStartRuntime()} disabled={saving || runtimeStarting}>{runtimeStarting ? "正在准备…" : "启动工作区"}</button>
                  ) : (
                    <>
                      {activeScope !== "general" && <button onClick={() => void onSwitchScope("general")} disabled={saving || runtimeStarting || projectSwitching}>返回通用会话</button>}
                      {activeScope === "general" && <button onClick={() => void onSwitchScope("scratch")} disabled={saving || runtimeStarting || projectSwitching}>新建 Scratch</button>}
                      <button className="primary" onClick={() => void onChooseRepo()} disabled={saving || runtimeStarting || projectSwitching}>选择 Git 项目</button>
                      {connection === "connected" && <button disabled={saving} onClick={() => requestLeave(onClose)}>完成</button>}
                    </>
                  )}
                </div>
              </details>}</section>
          </SettingsTargetSection></div>}
          {visited.has("permissions") && <div hidden={section !== "permissions"} className="settings-page">
            <SettingsTargetSection owner="workspace" lease={runtimeSettings}><div id="setting-trust" className={anchorClass("trust")} tabIndex={-1}>        <h3 className="settings-subhead">授权级别 <small>Agent 动手前要不要先问你</small></h3>
              {!trustView && (
                <p className="settings-intro">授权级别按工作区保存：连接到要调整的个人空间或项目后，在这里核对该工作区的权限，也可以在输入框下方切换。</p>
              )}
              {trustView && (
                <section className="trust-card" aria-label="授权级别">
                  <div className="trust-head">
                    <div>
                      <span>授权级别</span>
                      <strong>{trustView.title}</strong>
                    </div>
                    <i>{trustView.hint}</i>
                  </div>
                  <div className="trust-options" role="radiogroup" aria-label="授权级别">
                    {trustView.options.map((option) => (
                      <button
                        key={option.level}
                        role="radio"
                        aria-checked={option.active}
                        className={option.active ? "active" : ""}
                        disabled={trustBusy || option.blocked}
                        title={option.blockedReason}
                        onClick={() => onRuntimeAction(() => onTrustChange(option.level))}
                      >{option.title}</button>
                    ))}
                  </div>
                  <ul className="trust-note">
                    {trustView.note.split(/。\s*/).filter((line) => line.trim()).map((line) => <li key={line}>{line}。</li>)}
                  </ul>
                </section>
              )}

            </div>
            </SettingsTargetSection>
            <SettingsTargetSection owner="desktop"><div id="setting-browser" className={anchorClass("browser")} tabIndex={-1}><BrowserPane onDraftStateChange={draftCallbacks.browser} onRestartRuntime={onRestartRuntime} /></div>
            <div id="setting-control" className={anchorClass("control")} tabIndex={-1}><DesktopControlPane onDraftStateChange={draftCallbacks.control} onRestartRuntime={onRestartRuntime} /></div>
            </SettingsTargetSection>
            <SettingsTargetSection owner="space" lease={runtimeSettings} unavailable={!runtimeSettings?.target.space}><section id="setting-schedule" className={`${anchorClass("schedule")} settings-card`} tabIndex={-1}><div className="settings-card-head"><strong>定时任务</strong>
              {onOpenSchedule && <button onClick={() => requestLeave(() => { onRuntimeAction(() => { onClose(); onOpenSchedule(); }); })}>查看计划</button>}</div>
              <p className="settings-card-note">在助手的「计划」中管理任务。定时任务由对应实例执行；启用浏览器或桌面操作不会扩大无人值守任务的权限。</p></section></SettingsTargetSection>
          </div>}
          {visited.has("tools") && <div hidden={section !== "tools"} className="settings-page">
            <SettingsTargetSection owner="desktop"><div id="setting-codex" className={anchorClass("codex")} tabIndex={-1}><CodexWatchCard hasResident={hasResident} readSnapshot={readCodexObserver} /></div>
            </SettingsTargetSection>
            <SettingsTargetSection owner="workspace" lease={runtimeSettings}><section id="setting-extensions" className={`${anchorClass("extensions")} settings-card`} tabIndex={-1}><div className="settings-card-head"><strong>MCP 工具服务</strong>
              {onOpenPlugins && <button onClick={() => requestLeave(() => { onRuntimeAction(() => { onClose(); onOpenPlugins(); }); })}>管理插件和 MCP</button>}</div>
              <p className="settings-card-note">连接文档检索、云服务等外部工具。安装和配置在插件页中管理。</p></section>
            </SettingsTargetSection>
            <div id="setting-project-extensions" className={anchorClass("project-extensions")} tabIndex={-1}><SettingsTargetSection owner="project" lease={runtimeSettings} unavailable={!runtimeSettings?.target.projectRoot}>
              {children || <p className="settings-intro">打开 Git 项目后，这里会显示该项目加载的规则、Skills、Hooks 与 MCP。</p>}
            </SettingsTargetSection></div>
          </div>}
          {visited.has("personal") && <div hidden={section !== "personal"} className="settings-page">
            <SettingsTargetSection owner="space" lease={runtimeSettings} unavailable={!runtimeSettings?.target.space}>{onEditAssistant && (
              <section id="setting-identity" className={`${anchorClass("identity")} settings-card`} tabIndex={-1} aria-label="助理资料">
                <div className="settings-card-head">
                  <strong>助理资料</strong>
                  <button onClick={() => requestLeave(() => { onRuntimeAction(() => { onClose(); onEditAssistant(); }); })}>编辑</button>
                </div>
                <p className="settings-card-note">助理的名字、性格（SOUL）和「关于你」。常驻电脑上的助理每次对话都会读。</p>
              </section>
            )}
            </SettingsTargetSection>
            <SettingsTargetSection owner="desktop"><div id="setting-instructions" className={anchorClass("instructions")} tabIndex={-1}><PersonalizePane onDraftStateChange={draftCallbacks.instructions} /></div>

            <section id="setting-appearance" tabIndex={-1} className={`${anchorClass("appearance")} appearance-card`} aria-label="外观">
              <div className="appearance-row">
                <div>
                  <strong>主题</strong>
                  <span>界面配色；跟随系统时随 macOS 的浅色/深色自动切换</span>
                </div>
                <div className="segmented" role="radiogroup" aria-label="主题">
                  {THEME_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      role="radio"
                      aria-checked={themePreference === option.value}
                      className={themePreference === option.value ? "active" : ""}
                      onClick={() => changeTheme(option.value)}
                    >{option.label}</button>
                  ))}
                </div>
              </div>
            </section>
            </SettingsTargetSection>
          </div>}
          {visited.has("staff") && <div hidden={section !== "staff"} className="settings-page"><SettingsTargetSection owner="space" lease={runtimeSettings} unavailable={!boundResidentId}>
            <StaffPane projectId={boundResidentId} client={runtimeSettings?.client ?? staffClient} />
          </SettingsTargetSection></div>}
          {visited.has("advanced") && <div hidden={section !== "advanced"} className="settings-page"><SettingsTargetSection owner="desktop">
            <div id="setting-version" className={anchorClass("version")} tabIndex={-1}>
              <UpdateCard /><details className="advanced-settings settings-about"><summary>版本与构建信息</summary><BuildVersion details /></details>
            </div><div id="setting-diagnostics" tabIndex={-1}><h3 className="settings-subhead">诊断日志</h3><DiagnosticsPane /></div>
            <div id="setting-telemetry" tabIndex={-1}><h3 className="settings-subhead">错误报告与使用统计</h3><TelemetryPane /></div>
            <div id="setting-files" className={anchorClass("files")} tabIndex={-1}><h3 className="settings-subhead">配置文件</h3><ConfigPane llmProfilePath={llmProfile?.configPath} /></div>
          </SettingsTargetSection></div>}
        </div>
        <footer className={`settings-savebar${section === "model" && modelDirty ? " settings-savebar-model-dirty" : ""}`}><div className="settings-save-summary">
          <div className="settings-save-feedback" role={modelFeedbackError ? "alert" : "status"}>
            {saving || section === "model" && llmProfileLoading ? <LoaderCircle size={16} aria-hidden="true" />
              : modelFeedbackError || pending.length || section === "model" && llmSaveKind === "pending" ? <CircleAlert size={16} aria-hidden="true" /> : <Check size={16} aria-hidden="true" />}
            <span><strong>{saving ? "正在处理…" : modelFeedback || (pending.length ? "有未保存的修改" : "当前没有未保存的修改")}</strong>
              <small>{saving ? "请等待当前操作完成" : pending.length ? "点击下方项目返回编辑；切换分类会保留草稿" : "各项分别保存；权限变更沿用原生确认"}</small></span>
          </div>
          {pendingChanges.length > 0 && <nav className="settings-pending-links" aria-label="未保存的设置">
            {pendingChanges.map(change => <button key={change.label} disabled={saving} onClick={() => navigate(change.page, change.anchor)} aria-label={`前往${change.label}`}>{change.label}<ChevronRight size={11} aria-hidden="true" /></button>)}
          </nav>}
        </div>
          {section === "model" && modelDirty && <button disabled={saving || modelUnavailable} onClick={resetModelDraft}>还原模型草稿</button>}
          {section === "model" && <button className="primary" disabled={saving || modelUnavailable || llmProfileConflict || !modelDirty || Boolean(modelState.missing)}
            title={modelState.missing || "保存到这台 Mac：默认模型、角色分工与自动调度配置"} onClick={() => void onSaveLlmProfile()}>{llmProfileBusy ? "保存中…" : "保存模型配置"}</button>}
          <button disabled={saving} onClick={() => requestLeave(onClose)}>完成</button>
        </footer>
      </div>
    </dialog>
  );
  return <>{typeof document === "undefined" ? content : createPortal(content, document.body)}
    {closeAction && <UnsavedSettingsDialog labels={pending} onStay={() => setCloseAction(null)} onDiscard={() => {
      resetModelDraft(); setCloseAction(null); closeAction();
    }} />}
  </>;
}

function UnsavedSettingsDialog({ labels, onStay, onDiscard }: { labels: string[]; onStay: () => void; onDiscard: () => void }) {
  const dialog = useDialogSurface();
  const content = <dialog ref={dialog} className="settings-unsaved" aria-labelledby="settings-unsaved-title"
    onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); if (event.nativeEvent.isComposing) event.preventDefault(); } }} onCancel={event => { event.preventDefault(); onStay(); }}>
    <h2 id="settings-unsaved-title">还有未保存的修改</h2><p>{labels.join("、")}尚未保存。关闭后会丢弃这些草稿，已完成的保存和权限变更不会撤销。</p>
    <div><button onClick={onDiscard}>放弃草稿并关闭</button><button className="primary" autoFocus onClick={onStay}>继续编辑</button></div>
  </dialog>;
  return typeof document === "undefined" ? content : createPortal(content, document.body);
}

/** 错误报告（默认开）与匿名使用统计（默认关）：改了立即保存，和隐私页同口径。 */
export function TelemetryPane({ load = loadTelemetrySettings, save = saveTelemetrySettings }: {
  load?: () => Promise<TelemetrySettings>; save?: (next: TelemetrySettings) => Promise<TelemetrySettings>;
}) {
  const [value, setValue] = useState<TelemetrySettings | null>(null);
  const [note, setNote] = useState("");
  useEffect(() => { void load().then(setValue).catch(() => setNote("读取设置失败")); }, [load]);
  const change = (next: TelemetrySettings) => {
    setValue(next); setNote("");
    void save(next).then(setValue).catch((error) => { setNote(error instanceof Error ? error.message : String(error)); void load().then(setValue); });
  };
  return <div className="settings-telemetry">
    <label><input type="checkbox" disabled={!value} checked={value?.errorsEnabled ?? true}
      onChange={event => value && change({ ...value, errorsEnabled: event.target.checked })} />
      <span><strong>自动发送错误报告</strong><small>崩溃、更新失败、内置引擎起不来时，把错误说明、App 和系统版本发给开发者。不含对话、文件内容和账号信息。</small></span></label>
    <label><input type="checkbox" disabled={!value} checked={value?.usageEnabled ?? false}
      onChange={event => value && change({ ...value, usageEnabled: event.target.checked })} />
      <span><strong>发送匿名使用统计</strong><small>每天各功能用了几次和 App 版本，用随机编号区分，不关联账号、不含任何内容。</small></span></label>
    <button type="button" className="link" onClick={() => void openUrl(PRIVACY_URL)}>隐私政策里怎么说</button>
    {note && <small role="status">{note}</small>}
  </div>;
}

/** 诊断日志：连接、切换、重连和界面报错记在本机，出问题时把文件发过来就能查。 */
function DiagnosticsPane() {
  const [note, setNote] = useState("");
  return <div className="settings-diagnostics">
    <p>连接常驻电脑、切换工作区、自动重连和界面报错会记在本机的 <code>~/Library/Logs/VortoOne/desktop.log</code>，
      不含对话内容和密钥。遇到连不上、没反应这类问题时，把这个文件发过来就能排查。</p>
    <button onClick={() => void invoke<string>("reveal_diagnostics_log")
      .then(() => setNote("已在 Finder 中显示"))
      .catch((error) => setNote(error instanceof Error ? error.message : String(error)))}>在 Finder 中显示</button>
    {note && <small role="status">{note}</small>}
  </div>;
}
