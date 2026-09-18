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
import type { ReactNode } from "react";

import { contextWindowSourceLabel, formatTokenCount } from "../lib/labels";
import { trustCard } from "../lib/trustCard";
import type {
  ConnectionState,
  DesktopLlmProfileStatus,
  GatewayProcessStatus,
  GatewayRecoveryRecord,
  TrustLevel,
  TrustStatus,
  WorkspaceScope,
} from "../types";

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
  llmProfileBusy: boolean;
  repoRoot: string;
  baseUrl: string;
  token: string;
  children?: ReactNode;
  onClose: () => void;
  onLlmBaseChange: (value: string) => void;
  onLlmModelChange: (value: string) => void;
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
  llmProfileBusy,
  repoRoot,
  baseUrl,
  token,
  children,
  onClose,
  onLlmBaseChange,
  onLlmModelChange,
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
  const llmInputIsLocal = /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?(\/|$)/i.test(llmBaseInput.trim());

  return (
    <div className="modal-backdrop">
      <section className="settings-modal">
        <div className="settings-heading">
          <div>
            <span>VortoCode Desktop</span>
            <h2>工作区与连接</h2>
          </div>
          <button onClick={onClose}>×</button>
        </div>
        <p className="settings-intro">
          {activeScope === "project"
            ? "VortoCode 会自动管理这个项目的本地引擎；通常不需要配置地址或手动连接。"
            : activeScope === "scratch"
              ? "Scratch 是应用管理的隔离 Git 工作区，适合生成、运行和测试临时代码。"
              : "普通任务直接在 General 中运行，不读取本机目录；需要文件时再创建 Scratch 或选择项目。"}
        </p>

        <section className={`llm-profile-card ${llmProfile?.configured ? "configured" : "unconfigured"}`}>
          <div className="llm-profile-head">
            <div>
              <span>模型服务</span>
              <strong>{llmProfile?.configured
                ? llmProfile.provider === "vortocode" ? "VortoCode Relay 已配置" : llmProfile.provider === "local" ? "本机模型已配置" : "自定义服务已配置"
                : "先配置模型，才能开始对话"}</strong>
            </div>
            <i>{llmProfile?.configured ? "Keychain" : "需要设置"}</i>
          </div>
          <div className="llm-profile-presets" aria-label="模型服务快捷设置">
            <button
              className={llmBaseInput.trim() === "https://token.vortotech.com/v1" ? "active" : ""}
              onClick={() => { onLlmBaseChange("https://token.vortotech.com/v1"); onLlmModelChange("mimo-v2.5"); onLlmKeyChange(""); }}
            >VortoCode Relay</button>
            <button
              className={llmBaseInput.trim() === "https://api.openai.com/v1" ? "active" : ""}
              onClick={() => { onLlmBaseChange("https://api.openai.com/v1"); onLlmModelChange(""); onLlmKeyChange(""); }}
            >OpenAI 兼容</button>
            <button
              className={llmInputIsLocal ? "active" : ""}
              onClick={() => { onLlmBaseChange("http://127.0.0.1:11434/v1"); onLlmModelChange(""); onLlmKeyChange(""); }}
            >本机模型</button>
          </div>
          <div className="llm-profile-fields">
            <label>
              <span>API Base</span>
              <input value={llmBaseInput} onChange={(event) => onLlmBaseChange(event.target.value)} placeholder="https://your-gateway.example/v1" />
            </label>
            <label>
              <span>模型名</span>
              <input value={llmModelInput} onChange={(event) => onLlmModelChange(event.target.value)} placeholder="服务中实际可用的模型名" />
            </label>
            <label>
              <span>API Key <em>{llmInputIsLocal ? "本机服务可留空" : "只写入 macOS Keychain"}</em></span>
              <input
                type="password"
                value={llmKeyInput}
                onChange={(event) => onLlmKeyChange(event.target.value)}
                autoComplete="new-password"
                placeholder={llmProfile?.configured ? "已安全保存；修改时输入新 Key" : "输入模型服务 Key"}
              />
            </label>
          </div>
          <div className={`llm-model-capability ${(llmProfile?.contextWindow ?? 0) > 0 ? "known" : "unknown"}`}>
            <span>模型上下文</span>
            <strong>{(llmProfile?.contextWindow ?? 0) > 0 ? `${formatTokenCount(llmProfile?.contextWindow)} tokens` : "保存时自动检测"}</strong>
            <em>{contextWindowSourceLabel(llmProfile?.contextWindowSource)}</em>
          </div>
          <p className="llm-profile-note">远程服务必须使用 HTTPS；本机 HTTP 仅允许 127.0.0.1 / localhost。保存后只重启当前 runtime，其他后台项目在下次启动时采用新配置。</p>
          <div className="llm-profile-actions">
            {llmProfile?.configured && <button onClick={() => void onClearLlmProfile()} disabled={llmProfileBusy}>清除配置</button>}
            <button
              className="primary"
              onClick={() => void onSaveLlmProfile()}
              disabled={llmProfileBusy || !llmBaseInput.trim() || !llmModelInput.trim() || (!llmInputIsLocal && !llmKeyInput.trim())}
            >{llmProfileBusy ? "正在应用…" : "保存并重启当前引擎"}</button>
          </div>
        </section>

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
            <p className="trust-note">{trustView.note}</p>
          </section>
        )}

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

        {children}

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
      </section>
    </div>
  );
}
