// App.tsx 抽出的「工作区与连接」设置弹窗（B8-④c S7，只搬家不改行为）。
//
// 表单草稿盘点结论（④c 红线：关弹窗不丢的草稿必须留 App）：LLM 三个输入
// （llmBaseInput/llmModelInput/llmKeyInput）与高级设置的 baseUrl/token 今天都是 App 级
// 持有、关弹窗不重置，且被 saveLlmProfile / connect 流程消费——**没有任何草稿能下移**，
// 本件是彻底的纯展示件。settingsOpen 与两条弹窗预取 effect（settingsOpen 触发拉
// llmProfile / 扩展检查）是 App 编排，留 App。
//
// ExtensionsInspector 卡经 children 槽位透传：它的 7 个 props 已在 App 接好，
// 不经本组件二次穿透（弹窗对卡内容保持无知）。
// 搬进来的唯一派生是 llmInputIsLocal（只服务本弹窗的三处 JSX）。
//
// 布局：左侧分组导航 + 搜索，右侧显示当前分页（参照 MiMo Desktop 的设置交互，独立实现）。
// 当前分页、搜索词和外观选择是纯本地 UI 态，留在组件内。
import { useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { errorText } from "../lib/errorText";
import type { ReactNode } from "react";
import { Eye, EyeOff } from "lucide-react";
import { BuildVersion } from "./BuildVersion";
import { applyModelPreset, modelSettingsState } from "../lib/modelSettings";

import { contextWindowSourceLabel, formatTokenCount } from "../lib/labels";
import { isLocalModelBase, PROVIDER_PRESETS, presetForBaseUrl } from "../lib/providers";
import { ConfigPane } from "./settings/ConfigPane";
import { LlmConnectionCheck } from "./settings/LlmConnectionCheck";
import { AccountPane } from "./settings/AccountPane";
import { BrowserPane } from "./settings/BrowserPane";
import { UpdateCard } from "./settings/UpdateCard";
import { DesktopControlPane } from "./settings/DesktopControlPane";
import { CustomProviders } from "./settings/CustomProviders";
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

export type SettingsSection = "account" | "general" | "usage" | "model" | "personalize" | "appearance" | "config" | "trust" | "browser" | "desktop" | "extensions";

/** 「账号」「使用情况」是作者自用中转的专属页：只有当前模型服务就是它（或已用它的账号登录）时才出现。 */
const RELAY_ONLY_SECTIONS = new Set<SettingsSection>(["account", "usage"]);

export function relayMode(profile: { provider?: string; account?: unknown } | null | undefined): boolean {
  return profile?.provider === "vortocode" || Boolean(profile?.account);
}

const SETTINGS_NAV: Array<{ group: string; items: Array<{ id: SettingsSection; label: string; keywords: string }> }> = [
  {
    group: "个人",
    items: [
      { id: "account", label: "账号", keywords: "登录 账号 退出 套餐 token plan 注册 vortocode" },
      { id: "general", label: "常规", keywords: "版本 构建 version 关于 工作区 项目 引擎 runtime 连接 恢复 scratch gateway token 高级" },
      { id: "usage", label: "使用情况", keywords: "用量 额度 剩余 计费 费用 quota usage billing" },
      { id: "model", label: "模型服务", keywords: "omlx 本地 端侧 获取模型列表 模型服务 供应商 api key relay openai deepseek kimi glm 千问 minimax 本机 测试连接 上下文 常用模型 推荐 排序 输入框" },
      { id: "personalize", label: "个性化", keywords: "全局指令 自定义指令 agents.md instructions" },
      { id: "appearance", label: "外观", keywords: "主题 深色 浅色 跟随系统 theme dark light" },
      { id: "config", label: "配置", keywords: "配置文件 路径 finder llm-profile agents.md" },
    ],
  },
  { group: "安全", items: [{ id: "trust", label: "授权级别", keywords: "权限 确认 只读 完全信任 trust" }] },
  {
    group: "集成",
    items: [
      { id: "browser", label: "浏览器操控", keywords: "浏览器 chrome 网页 点击 截图 browser" },
      { id: "desktop", label: "桌面操作（实验）", keywords: "桌面 linux x11 xdotool 截屏 窗口 点击 ssh desktop 实验" },
      { id: "extensions", label: "扩展", keywords: "hook 规则 skills mcp 插件 扩展" },
    ],
  },
];

type SettingsModalProps = {
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
  const [section, setSection] = useState<SettingsSection>(
    initialSection && (relayMode(llmProfile) || !RELAY_ONLY_SECTIONS.has(initialSection))
      ? initialSection
      : llmProfile && !llmProfile.configured ? "model" : "general");
  const [query, setQuery] = useState("");
  const [themePreference, setThemePreference] = useState<ThemePreference>(readThemePreference);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const activePreset = presetForBaseUrl(llmBaseInput);
  const [showKey, setShowKey] = useState(false);
  // 实验功能：外部执行者（Desktop 偏好，默认关闭）。打开要过原生确认，开关后重启当前引擎才生效。
  const [experimental, setExperimental] = useState(false);
  const [experimentalBusy, setExperimentalBusy] = useState(false);
  const [experimentalNote, setExperimentalNote] = useState("");
  useEffect(() => {
    let alive = true;
    invoke<boolean>("get_experimental_workers").then((value) => { if (alive) setExperimental(Boolean(value)); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  const toggleExperimental = async (next: boolean) => {
    setExperimentalBusy(true);
    setExperimentalNote("");
    try {
      setExperimental(await invoke<boolean>("set_experimental_workers", { enabled: next }));
      const restarted = await onRestartRuntime();
      setExperimentalNote(restarted ? (next ? "已开启，当前引擎已重启" : "已关闭，当前引擎已重启") : "已保存，下次启动引擎时生效");
    } catch (failure) {
      setExperimentalNote(errorText(failure, "切换实验功能失败"));
    } finally {
      setExperimentalBusy(false);
    }
  };
  const draft = { baseUrl: llmBaseInput, model: llmModelInput, apiKey: llmKeyInput, fastModel: llmFastInput, strongModel: llmStrongInput, roleModels: llmRolesInput };
  const modelState = modelSettingsState(llmProfile, draft);
  const choosePreset = (preset: typeof PROVIDER_PRESETS[number]) => {
    const next = applyModelPreset(draft, preset);
    if (next === draft) return;
    onLlmBaseChange(next.baseUrl); onLlmModelChange(next.model); onLlmKeyChange(next.apiKey);
    setShowKey(false); setModelOptions([]);
  };
  const relay = relayMode(llmProfile);
  const visibleNav = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const nav = SETTINGS_NAV
      .map((group) => ({ ...group, items: group.items.filter((item) => relay || !RELAY_ONLY_SECTIONS.has(item.id)) }))
      .filter((group) => group.items.length > 0);
    if (!keyword) return nav;
    return nav
      .map((group) => ({
        ...group,
        items: group.items.filter((item) => `${item.label} ${item.keywords}`.toLowerCase().includes(keyword)),
      }))
      .filter((group) => group.items.length > 0);
  }, [query, relay]);
  const sectionTitle = SETTINGS_NAV.flatMap((group) => group.items).find((item) => item.id === section)?.label ?? "";
  const changeTheme = (preference: ThemePreference) => {
    setThemePreference(preference);
    saveThemePreference(preference);
    applyThemePreference(preference);
  };

  return (
    <div className="modal-backdrop">
      <section className="settings-modal settings-layout" role="dialog" aria-modal="true" aria-label="设置">
        <nav className="settings-nav" aria-label="设置分类">
          <h2>设置</h2>
          <input
            className="settings-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索设置"
            aria-label="搜索设置"
          />
          {visibleNav.map((group) => (
            <div className="settings-nav-group" key={group.group}>
              <span>{group.group}</span>
              {group.items.map((item) => (
                <button
                  key={item.id}
                  className={section === item.id ? "active" : ""}
                  aria-current={section === item.id ? "page" : undefined}
                  onClick={() => setSection(item.id)}
                >{item.label}</button>
              ))}
            </div>
          ))}
          {visibleNav.length === 0 && <p className="settings-nav-empty">没有匹配的设置</p>}
        </nav>
        <div className="settings-pane">
        <div className="settings-heading">
          <h2>{sectionTitle}</h2>
          <button onClick={onClose} aria-label="关闭设置">×</button>
        </div>
        {section === "general" && (
        <>

        <p className="settings-intro">
          {activeScope === "project"
            ? "当前项目的连接由应用管理。"
            : activeScope === "scratch"
              ? "临时工作区适合生成、运行和测试代码。"
              : "可以直接对话；需要处理项目文件时，再选择项目。"}
        </p>
        </>
        )}

        {section === "model" && (
        <section className={`llm-profile-card ${llmProfile?.configured ? "configured" : "unconfigured"}`}>
          <div className="llm-profile-head">
            <div>
              <span>已保存的模型服务</span>
              <strong>{llmProfile?.configured
                ? llmProfile.provider === "vortocode" ? "VortoCode Relay 已配置" : llmProfile.provider === "local" ? "本机模型已配置" : "自定义服务已配置"
                : "先配置模型，才能开始对话"}</strong>
            </div>
            <i>{llmProfile?.account ? "账号登录" : llmProfile?.configured ? "本机配置" : "需要设置"}</i>
          </div>
          {llmProfile?.account && (
            <p className="llm-account-note">
              当前通过账号 {llmProfile.account.displayName || llmProfile.account.username} 登录，使用{llmProfile.account.keySource === "token_plan" ? " Token Plan" : "按量 Key"}。
              在下面改服务地址或填新 Key 会替换掉它；要换账号请到<button onClick={() => setSection("account")}>账号</button>。
            </p>
          )}
          <p className="llm-setup-guide">填写服务地址 → 获取模型列表 → 选择模型 → 保存应用。任何 OpenAI 兼容服务都可以；本地模型不需要 Key。</p>
          <p className="llm-profile-note">快捷项只用于预填地址；切换本机快捷项会保留你已经填写的本机端口和模型。</p>
          <div className="llm-profile-presets" aria-label="模型服务快捷设置">
            {PROVIDER_PRESETS.map((preset) => (
              <button
                key={preset.id}
                className={activePreset?.id === preset.id ? "active" : ""}
                onClick={() => choosePreset(preset)}
              >{preset.label}</button>
            ))}
            <button className={!activePreset && llmBaseInput.trim() ? "active" : ""} onClick={() => { onLlmBaseChange(""); onLlmModelChange(""); onLlmKeyChange(""); setModelOptions([]); }}>自定义</button>
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
              /><button type="button" aria-label={showKey ? "隐藏 API Key" : "显示 API Key"} onClick={() => setShowKey(!showKey)}>{showKey ? <EyeOff size={16}/> : <Eye size={16}/>}</button></div>
            </label>
          </div>
          <div className={`llm-model-capability ${modelState.sameModel && (llmProfile?.contextWindow ?? 0) > 0 ? "known" : "unknown"}`}>
            <span>模型上下文</span>
            <strong>{modelState.sameModel && (llmProfile?.contextWindow ?? 0) > 0 ? `${formatTokenCount(llmProfile?.contextWindow)} tokens` : "当前草稿尚未检测"}</strong>
            <em>{modelState.sameModel ? contextWindowSourceLabel(llmProfile?.contextWindowSource) : "旧配置的信息不适用于此草稿"}</em>
          </div>
          <p className="llm-profile-note">远程服务必须使用 HTTPS；本机 HTTP 仅允许 127.0.0.1 / localhost。保存后只重启当前 runtime，其他后台项目在下次启动时采用新配置。</p>
          {llmProfile?.configPath && (
            <p className="llm-profile-note">配置文件：<code>{llmProfile.configPath}</code>（也可直接编辑，重启 Desktop 后生效）</p>
          )}
          <LlmConnectionCheck baseUrl={llmBaseInput} apiKey={llmKeyInput} model={llmModelInput} onModels={setModelOptions} />
          <p className="llm-save-status" role="status">{modelState.missing || (modelState.dirty ? "修改尚未保存，不会影响当前对话。" : "与已保存配置一致；模型列表可达不等于推理或工具已验证。")}</p>
          <div className="llm-profile-actions">
            {llmProfile?.configured && <button onClick={() => void onClearLlmProfile()} disabled={llmProfileBusy}>清除配置</button>}
            <button
              className="primary"
              onClick={() => void onSaveLlmProfile()}
              disabled={llmProfileBusy || Boolean(modelState.missing)}
            >{llmProfileBusy ? "正在应用…" : "保存并重启当前引擎"}</button>
          </div>
          <details className="llm-extra-settings"><summary>高级模型选项（可选）</summary>
          <ModelRoles profile={llmProfile} value={llmRolesInput} onChange={onLlmRolesChange} disabled={llmProfileBusy} engine={engineWorkers}
            experimental={experimental} experimentalBusy={experimentalBusy} experimentalNote={experimentalNote} onExperimentalToggle={(next) => void toggleExperimental(next)} />
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
          <p className="llm-profile-note">快速与强力模型随上方保存一起应用；下方常用模型名单独生效。</p>
          <ModelPicks profile={llmProfile} picks={modelPicks} onChange={onModelPicksChange} />
          </details>
          <details className="llm-extra-settings"><summary>其他模型供应商（可选）</summary>
          <CustomProviders profile={llmProfile} onProfile={onLlmProfileChange} onRestartRuntime={onRestartRuntime} />
          </details>
        </section>
        )}

        {section === "account" && (
          <AccountPane
            profile={llmProfile}
            onProfile={onLlmProfileChange}
            onRestartRuntime={onRestartRuntime}
            onOpenModelSettings={() => setSection("model")}
          />
        )}
        {section === "usage" && <UsagePane account={llmProfile?.account} />}
        {section === "personalize" && <PersonalizePane />}
        {section === "config" && <ConfigPane llmProfilePath={llmProfile?.configPath} />}

        {section === "appearance" && (
          <section className="appearance-card" aria-label="外观">
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
        )}

        {section === "trust" && !trustView && (
          <p className="settings-intro">连接当前运行服务后可设置授权级别；旧服务可能不支持。</p>
        )}
        {section === "trust" && trustView && (
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
            <p className="trust-note">{trustView.note}</p>
          </section>
        )}

        {section === "general" && (
        <>
        <label>
          <span>{activeScope === "project" ? "Git 项目" : "可选项目"}</span>
          <div className="field-row">
            <input value={repoRoot} readOnly placeholder="尚未选择项目" />
            <button onClick={() => void onChooseRepo()} disabled={projectSwitching || runtimeStarting}>选择…</button>
          </div>
        </label>

        <div className={`process-card ${connection === "connected" ? "running" : ""}`}>
          <span className="process-indicator" />
          <div>
            <strong>{runtimeStarting ? "正在准备本地引擎" : connection === "connected" ? connectionText : "本地引擎尚未就绪"}</strong>
            <p>{runtimeStarting ? "正在启动内置引擎并恢复会话…" : connectionNote}</p>
          </div>
        </div>

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
        )}

        {section === "browser" && <BrowserPane onRestartRuntime={onRestartRuntime} />}
        {section === "desktop" && <DesktopControlPane onRestartRuntime={onRestartRuntime} />}
        {section === "extensions" && (children || (
          <p className="settings-intro">打开 Git 项目后，这里会显示该项目加载的规则、Skills、Hooks 与 MCP。</p>
        ))}

        {section === "general" && (
        <>
        <UpdateCard />
        <details className="settings-about"><summary>版本与构建信息</summary><BuildVersion details /></details>
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
            只允许 127.0.0.1 / localhost。{processStatus.running && processStatus.command ? `当前引擎：${processStatus.command}` : "远程连接尚未开放。"}
          </div>
          <div className="advanced-actions">
            {processStatus.running && <button className="danger" onClick={() => void onStopRuntime()} disabled={runtimeStarting}>停止本地引擎</button>}
            <button onClick={() => void onConnectExisting()} disabled={!repoRoot.trim() || runtimeStarting}>连接已有实例</button>
          </div>
        </details>

        <div className="settings-actions">
          {activeScope === "project" && repoRoot.trim() ? (
            connection === "connected"
              ? <button className="primary" onClick={onClose}>完成</button>
              : <button className="primary" onClick={() => void onStartRuntime()} disabled={runtimeStarting}>{runtimeStarting ? "正在准备…" : "启动工作区"}</button>
          ) : (
            <>
              {activeScope !== "general" && <button onClick={() => void onSwitchScope("general")} disabled={runtimeStarting || projectSwitching}>返回通用会话</button>}
              {activeScope === "general" && <button onClick={() => void onSwitchScope("scratch")} disabled={runtimeStarting || projectSwitching}>新建 Scratch</button>}
              <button className="primary" onClick={() => void onChooseRepo()} disabled={runtimeStarting || projectSwitching}>选择 Git 项目</button>
              {connection === "connected" && <button onClick={onClose}>完成</button>}
            </>
          )}
        </div>
        </>
        )}
        </div>
      </section>
    </div>
  );
}
