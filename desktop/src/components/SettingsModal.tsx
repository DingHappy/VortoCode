// Keep real settings endpoints and native confirmation flows; page navigation retains unsaved forms.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { SETTINGS_SECTIONS, searchSettings, settingsPage, type SettingsPage, type SettingsSection } from "../lib/settingsNavigation";
import "./SettingsModal.css";
export type { SettingsSection } from "../lib/settingsNavigation";
import { invoke } from "@tauri-apps/api/core";
import { errorText } from "../lib/errorText";
import type { CodexObserverRead } from "../lib/codexObserverHealth";
import type { ReactNode } from "react";
import { UserRound, Cpu, Monitor, ShieldCheck, Blocks, SlidersHorizontal, Info, Search, X, MapPin, ChevronRight, Check, CircleAlert, Circle, LoaderCircle } from "lucide-react";
import { Eye, EyeOff } from "lucide-react";
import { BuildVersion } from "./BuildVersion";
import { restartSavedSetting } from "../lib/settingsApply";
import type { ModelSaveKind } from "../lib/modelProfileSave";
import { applyModelPreset, modelSettingsState } from "../lib/modelSettings";

import { contextWindowSourceLabel, formatTokenCount } from "../lib/labels";
import { isLocalModelBase, PROVIDER_PRESETS } from "../lib/providers";
import { ConfigPane } from "./settings/ConfigPane";
import { LlmConnectionCheck } from "./settings/LlmConnectionCheck";
import { AccountPane } from "./settings/AccountPane";
import { BrowserPane } from "./settings/BrowserPane";
import { UpdateCard } from "./settings/UpdateCard";
import { VortoAccountCard } from "./settings/VortoAccountCard";
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

const NAV_ICONS = { account: UserRound, model: Cpu, computer: Monitor, permissions: ShieldCheck, tools: Blocks, personal: SlidersHorizontal, advanced: Info };
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
  readCodexObserver?: () => Promise<CodexObserverRead>;
  /** 打开助理资料（名字、SOUL、关于你） */
  onEditAssistant?: () => void;
  residentName?: string;
  residentConnected?: boolean;
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
  readCodexObserver,
  onEditAssistant,
  residentName, residentConnected = false, onOpenResidentSetup, onConnectResident, onOpenSchedule, llmSaveNote = "", llmSaveKind = "idle", llmProfileLoading = false, llmProfileError = "", llmProfileConflict = false, onReloadLlmProfile,
  activeScope,
  connection,
  connectionText,
  connectionNote,
  runtimeStarting,
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
  onRestartRuntime,
  onLlmProfileChange,
  modelPicks,
  onModelPicksChange,
  initialSection,
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
  const trustView = trustCard(trust);
  const llmInputIsLocal = isLocalModelBase(llmBaseInput);
  const [section, setSection] = useState<SettingsPage>(settingsPage(
    initialSection ?? (llmProfile && !llmProfile.configured ? "model" : "account")));
  const [visited, setVisited] = useState<Set<SettingsPage>>(() => new Set([section]));
  const [highlight, setHighlight] = useState("");
  const dialog = useDialogSurface();
  const contentRef = useRef<HTMLDivElement>(null);
  const [closeAction, setCloseAction] = useState<(() => void) | null>(null);
  const [drafts, setDrafts] = useState<Record<string, DraftState>>({});
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
  const modelUnavailable = llmProfileLoading || Boolean(llmProfileError);
  const modelConflict = llmProfileConflict || Boolean(drafts.provider?.conflict);
  const modelReadMessage = llmProfileLoading ? "正在读取本机模型配置…" : llmProfileError;
  const modelFeedback = section === "model" ? modelReadMessage || (llmProfileConflict ? "已保存配置有更新，请先核对最新配置" : llmSaveNote) : "";
  const modelFeedbackError = section === "model" && (Boolean(llmProfileError) || llmProfileConflict || llmSaveKind === "error");
  const saving = recoveryBusy || llmProfileBusy || experimentalBusy || trustBusy || Object.values(drafts).some(state => state.busy);
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
        <div className="settings-brand"><SlidersHorizontal size={18} /><h2>设置</h2><span>VortoCode</span></div>
        <div className="settings-search-wrap"><Search size={15} aria-hidden="true" /><input type="search" className="settings-search" value={query}
          onChange={event => setQuery(event.target.value)} placeholder="搜索设置项" aria-label="搜索设置" /></div>
        <div className="settings-nav-group">
          {query.trim() ? searchResults.map(item => {
            const Icon = NAV_ICONS[item.page];
            return <button key={item.id} className="settings-search-result" onClick={() => navigate(item.page, item.id)}>
              <Icon size={17} aria-hidden="true" /><span>{item.label}<small>{SETTINGS_SECTIONS.find(page => page.id === item.page)?.label}</small></span><ChevronRight size={14} aria-hidden="true" />
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
        <div className="settings-nav-footer"><Monitor size={15} /><div>这台 Mac 的设置<small>常驻实例单独配置</small></div></div>
      </nav>
      <div className="settings-pane">
        <header className="settings-heading"><div><h2 id="settings-page-title">{currentPage.label}</h2><p>{currentPage.description}</p></div>
          <button className="settings-close" disabled={saving} onClick={() => requestLeave(onClose)} aria-label="关闭设置"><X size={18} /></button></header>
        <div className="settings-content" ref={contentRef} aria-labelledby="settings-page-title">
          <div className="settings-scope"><MapPin size={16} aria-hidden="true" /><p>{currentPage.scope}</p></div>
          {visited.has("account") && <div hidden={section !== "account"} className="settings-page">
            <div id="setting-account" className={anchorClass("account")} tabIndex={-1}>
              <VortoAccountCard
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
            </div>
          </div>}
          {visited.has("model") && <div hidden={section !== "model"} className="settings-page" aria-busy={llmProfileLoading}>
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
          </div>}
          {visited.has("computer") && <div hidden={section !== "computer"} className="settings-page">
            <section id="setting-resident" className={`${anchorClass("resident")} settings-card`} tabIndex={-1} aria-label="常驻助手连接">
              <div className="settings-card-head"><strong>常驻助手</strong><span className="settings-scope-tag">独立实例</span></div>
              <div className="settings-connection-row"><div><strong>{residentName || (hasResident ? "已登记常驻电脑" : "尚未登记常驻电脑")}</strong>
                <p>{residentConnected ? "当前对话已连接到常驻实例" : hasResident ? "已登记；当前对话未连接到该实例" : "连接专用电脑上的助手服务，让它在远端持续运行。"}</p></div>
                {hasResident && onConnectResident && !residentConnected && <button onClick={() => requestLeave(() => { onClose(); onConnectResident(); })}>连接助手</button>}
                {onOpenResidentSetup && <button onClick={() => requestLeave(() => { onClose(); onOpenResidentSetup(); })}>{hasResident ? "添加常驻连接" : "连接常驻电脑"}</button>}</div>
              <p className="settings-card-note">助手服务的连接不代表远程画面已连接，也不会自动启用桌面操作。</p>
            </section>
            <div id="setting-desktop" className={anchorClass("desktop")} tabIndex={-1}><ResidentDesktopPane onDraftStateChange={draftCallbacks.desktop} /></div>
            <section id="setting-runtime" className={`${anchorClass("runtime")} settings-card`} tabIndex={-1}><div className="settings-card-head"><strong>本机引擎与工作区</strong><span className="settings-scope-tag">这台 Mac</span></div>
              <p className="settings-runtime-status">{runtimeStarting ? "正在准备本机引擎…" : processStatus.running ? "本机引擎运行中" : "本机引擎未运行"}</p>
              <details className="settings-runtime-details"><summary>工作区与连接诊断</summary>
                <>
                  <label>
                    <span>{activeScope === "project" ? "Git 项目" : "可选项目"}</span>
                    <div className="field-row">
                      <input value={repoRoot} readOnly placeholder="尚未选择项目" />
                      <button onClick={() => void onChooseRepo()} disabled={projectSwitching || runtimeStarting}>选择…</button>
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
                        ? "上次工作区意外停止，VortoCode 可以重新启动并恢复项目配置。"
                        : "检测到上次没有完成退出清理。VortoCode 会先尝试恢复，失败后启动新的本地引擎。"}</p>
                      <code title={recoveryRecord.repoRoot}>{recoveryRecord.repoRoot}</code>
                      <div className="recovery-actions">
                        <button onClick={() => void onDismissRecovery()}>忽略记录</button>
                        <button className="primary" onClick={() => void onRestoreRecovery()}>恢复工作区</button>
                      </div>
                    </div>
                  )}

                </>

                <details className="advanced-settings">
                  <summary>高级连接设置</summary>
                  <p>仅在连接手动启动的 `vc server` 或排查本地引擎时使用。</p>
                  <label>
                    <span>Gateway 地址</span>
                    <input value={baseUrl} onChange={(event) => onBaseUrlChange(event.target.value)} placeholder="http://127.0.0.1:8080" />
                  </label>
                  <label>
                    <span>API Token <em>仅保存在当前进程内</em></span>
                    <input type="password" value={token} onChange={(event) => onTokenChange(event.target.value)} placeholder="本机无 token 时留空" />
                  </label>
                  <div className="settings-note">
                    只允许 127.0.0.1 / localhost。{processStatus.running && processStatus.command ? `当前引擎：${processStatus.command}` : "此入口只连接本机；常驻电脑使用上方的助手连接。"}
                  </div>
                  <div className="advanced-actions">
                    {processStatus.running && <button className="danger" onClick={() => void onStopRuntime()} disabled={runtimeStarting}>停止本地引擎</button>}
                    <button onClick={() => void onConnectExisting()} disabled={!repoRoot.trim() || runtimeStarting}>连接已有实例</button>
                  </div>
                </details>

                <div className="settings-actions">
                  {activeScope === "project" && repoRoot.trim() ? (
                    connection === "connected"
                      ? <button className="primary" onClick={() => requestLeave(onClose)}>完成</button>
                      : <button className="primary" onClick={() => void onStartRuntime()} disabled={runtimeStarting}>{runtimeStarting ? "正在准备…" : "启动工作区"}</button>
                  ) : (
                    <>
                      {activeScope !== "general" && <button onClick={() => void onSwitchScope("general")} disabled={runtimeStarting || projectSwitching}>返回通用会话</button>}
                      {activeScope === "general" && <button onClick={() => void onSwitchScope("scratch")} disabled={runtimeStarting || projectSwitching}>新建 Scratch</button>}
                      <button className="primary" onClick={() => void onChooseRepo()} disabled={runtimeStarting || projectSwitching}>选择 Git 项目</button>
                      {connection === "connected" && <button onClick={() => requestLeave(onClose)}>完成</button>}
                    </>
                  )}
                </div>
              </details></section>
          </div>}
          {visited.has("permissions") && <div hidden={section !== "permissions"} className="settings-page">
            <div id="setting-trust" className={anchorClass("trust")} tabIndex={-1}>        <h3 className="settings-subhead">授权级别 <small>Agent 动手前要不要先问你</small></h3>
              {!trustView && (
                <p className="settings-intro">授权级别按工作区保存：在工作台打开一个对话、连上引擎后就能在这里调整，也可以直接在输入框下方切换。</p>
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
                        onClick={() => onTrustChange(option.level)}
                      >{option.title}</button>
                    ))}
                  </div>
                  <ul className="trust-note">
                    {trustView.note.split(/。\s*/).filter((line) => line.trim()).map((line) => <li key={line}>{line}。</li>)}
                  </ul>
                </section>
              )}

            </div>
            <div id="setting-browser" className={anchorClass("browser")} tabIndex={-1}><BrowserPane onDraftStateChange={draftCallbacks.browser} onRestartRuntime={onRestartRuntime} /></div>
            <div id="setting-control" className={anchorClass("control")} tabIndex={-1}><DesktopControlPane onDraftStateChange={draftCallbacks.control} onRestartRuntime={onRestartRuntime} /></div>
            <section id="setting-schedule" className={`${anchorClass("schedule")} settings-card`} tabIndex={-1}><div className="settings-card-head"><strong>定时任务</strong>
              {onOpenSchedule && <button onClick={() => requestLeave(() => { onClose(); onOpenSchedule(); })}>查看计划</button>}</div>
              <p className="settings-card-note">在助手的「计划」中管理任务。定时任务由对应实例执行；启用浏览器或桌面操作不会扩大无人值守任务的权限。</p></section>
          </div>}
          {visited.has("tools") && <div hidden={section !== "tools"} className="settings-page">
            <div id="setting-codex" className={anchorClass("codex")} tabIndex={-1}><CodexWatchCard hasResident={hasResident} readSnapshot={readCodexObserver} /></div>
            <section id="setting-extensions" className={`${anchorClass("extensions")} settings-card`} tabIndex={-1}><div className="settings-card-head"><strong>MCP 工具服务</strong>
              {onOpenPlugins && <button onClick={() => requestLeave(() => { onClose(); onOpenPlugins(); })}>管理插件和 MCP</button>}</div>
              <p className="settings-card-note">连接文档检索、云服务等外部工具。安装和配置在插件页中管理。</p></section>
            {children || <p className="settings-intro">打开 Git 项目后，这里会显示该项目加载的规则、Skills、Hooks 与 MCP。</p>}
          </div>}
          {visited.has("personal") && <div hidden={section !== "personal"} className="settings-page">
            {onEditAssistant && (
              <section id="setting-identity" className={`${anchorClass("identity")} settings-card`} tabIndex={-1} aria-label="助理资料">
                <div className="settings-card-head">
                  <strong>助理资料</strong>
                  <button onClick={() => requestLeave(() => { onClose(); onEditAssistant(); })}>编辑</button>
                </div>
                <p className="settings-card-note">助理的名字、性格（SOUL）和「关于你」。常驻电脑上的助理每次对话都会读。</p>
              </section>
            )}
            <div id="setting-instructions" className={anchorClass("instructions")} tabIndex={-1}><PersonalizePane onDraftStateChange={draftCallbacks.instructions} /></div>

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


          </div>}
          {visited.has("advanced") && <div hidden={section !== "advanced"} className="settings-page">
            <div id="setting-version" className={anchorClass("version")} tabIndex={-1}>
              <UpdateCard /><details className="advanced-settings settings-about"><summary>版本与构建信息</summary><BuildVersion details /></details>
            </div><div id="setting-files" className={anchorClass("files")} tabIndex={-1}><h3 className="settings-subhead">配置文件</h3><ConfigPane llmProfilePath={llmProfile?.configPath} /></div>
          </div>}
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
            title={modelState.missing || "保存默认模型、角色分工与自动调度配置"} onClick={() => void onSaveLlmProfile()}>{llmProfileBusy ? "保存中…" : "保存模型配置"}</button>}
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
