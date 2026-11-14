import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, ArrowLeft, Check, LoaderCircle } from "lucide-react";
import type { DesktopProjectProfile } from "../types";
import { isAssistantHost } from "../lib/assistantHost";
import { completeRuntimeSetup, writeRuntimeSetup, type RuntimeSetupRecord } from "../lib/runtimeOnboarding";
import { type LocalResidentClient, type LocalResidentStatus } from "../lib/localResident";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { LocalResidentCard } from "./settings/LocalResidentCard";
import { RemoteProjectDialog } from "./RemoteProjectDialog";
import { RuntimeLocationChoices } from "./RuntimeLocationChoices";
import { PreferenceDetails } from "./ui";
import "./RuntimeOnboarding.css";

export interface RuntimeOnboardingProps {
  initial: RuntimeSetupRecord;
  initialError?: string;
  inspectionFailed?: boolean;
  restoreTarget?: DesktopProjectProfile;
  profiles: DesktopProjectProfile[];
  modelReady: boolean;
  modelBusy?: boolean;
  modelSetup: ReactNode;
  localClient?: LocalResidentClient;
  onConnect: (profile: DesktopProjectProfile) => Promise<void>;
  onComplete: (record: RuntimeSetupRecord) => void;
  onStatus: (status: LocalResidentStatus) => void;
  onCancel?: () => void;
  onInspectAgain: () => void;
}

export function RuntimeOnboarding({ initial, initialError = "", inspectionFailed = false, restoreTarget, profiles, modelReady, modelBusy = false, modelSetup, localClient, onConnect, onComplete, onStatus, onCancel, onInspectAgain }: RuntimeOnboardingProps) {
  const dialog = useDialogSurface();
  const heading = useRef<HTMLHeadingElement>(null);
  const [choice, setChoice] = useState<"local" | "remote" | null>(initial.status === "pending" ? initial.choice : null);
  const [restoring, setRestoring] = useState(Boolean(restoreTarget));
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [local, setLocal] = useState<LocalResidentStatus | null>(null);
  const [editingModel, setEditingModel] = useState(false);
  const alive = useRef(true), flight = useRef(false), autoRestored = useRef(false);
  const callbacks = useRef({ onConnect, onComplete, onStatus }); callbacks.current = { onConnect, onComplete, onStatus };
  const blocked = busy || localBusy || remoteBusy || modelBusy;
  const localPrepared = Boolean(local?.installed || modelReady && !editingModel);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { heading.current?.focus(); }, [choice, restoring]);
  useEffect(() => { if (modelReady) setEditingModel(false); }, [modelReady]);

  async function finish(profile: DesktopProjectProfile) {
    if (flight.current) throw new Error("正在连接，请稍候");
    flight.current = true; setBusy(true); setError("");
    try {
      const record = await completeRuntimeSetup(profile, p => callbacks.current.onConnect(p), localStorage, () => alive.current);
      if (alive.current) callbacks.current.onComplete(record);
    } finally {
      flight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const connectKnown = (profile: DesktopProjectProfile) => { void finish(profile).catch(cause => { if (alive.current) setError(cause instanceof Error ? cause.message : String(cause)); }); };
  useEffect(() => {
    if (restoreTarget && !autoRestored.current) { autoRestored.current = true; connectKnown(restoreTarget); }
    // Restore once; callbacks are refreshed without restarting an in-flight connection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  function choose(next: "local" | "remote" | null) {
    if (blocked || inspectionFailed) return;
    try {
      writeRuntimeSetup(localStorage, { version: 1, status: "pending", choice: next });
      setChoice(next); setRestoring(false); setError("");
    } catch { setError("无法保存引导进度，请检查本机存储后重试。"); }
  }
  const targetName = restoreTarget?.name || "上次选择的助手";
  return <dialog ref={dialog} className="runtime-onboarding" aria-labelledby="runtime-onboarding-title" onCancel={event => { event.preventDefault(); if (!blocked && onCancel) onCancel(); }}>
    <div className="onboarding-shell">
      <header className="onboarding-header"><strong>VortoOne</strong>{onCancel && <button disabled={blocked} onClick={onCancel}>返回当前助手</button>}</header>
      {(choice || restoring) && <ol className="onboarding-steps" aria-label="设置进度"><li>运行位置</li>
        <li aria-current={choice === "local" && localPrepared ? undefined : "step"}>{choice === "local" ? "准备模型" : "连接助手"}</li>
        <li aria-current={choice === "local" && localPrepared ? "step" : undefined}>{choice === "local" ? "安装与连接" : "开始使用"}</li></ol>}
      <h1 ref={heading} tabIndex={-1} id="runtime-onboarding-title">{restoring ? `打开${targetName}` : !choice ? "让助理在哪里运行？" : choice === "local" ? "准备这台 Mac" : "连接已有助手"}</h1>
      <p className="onboarding-lead">{restoring ? "连接后继续原来的对话。" : !choice ? "选择一台电脑，保存你的资料，运行你的个人助理。" : choice === "local" ? "准备模型和程序，然后启动你的个人助理。" : "使用那台电脑已有的模型和资料。连接前由你核对。"}</p>
      {error && <div role="alert" className="onboarding-error"><p>{error}</p>{inspectionFailed && <button onClick={onInspectAgain}>重新核对已有配置</button>}</div>}
      {restoring && restoreTarget ? <section className="onboarding-panel"><strong>{targetName}</strong><p>原来的对话与资料仍属于这份助手。</p><button className="primary" disabled={blocked} onClick={() => connectKnown(restoreTarget)}>{busy ? <><LoaderCircle className="activity-spinner" />正在连接…</> : "重新连接这份助手"}</button></section>
        : !choice ? <><RuntimeLocationChoices disabled={inspectionFailed} onChoose={choose} />
          <PreferenceDetails label="如何选择运行位置？"><p>经常开机的电脑适合让助理持续工作。只在这台 Mac 使用时，可以选择本机运行。</p><p>每个运行位置保留自己的对话和资料；切换不会自动搬动或合并原资料。</p></PreferenceDetails>
        </> : choice === "local" ? <>
          {local?.supported && !local.installed && (modelReady && !editingModel
            ? <section className="onboarding-model-saved" aria-label="模型配置已保存"><Check aria-hidden="true" /><div><strong>模型配置已保存</strong><p>安装时仍需你确认复制，之后由常驻助理独立管理。</p></div><button type="button" disabled={blocked} onClick={() => setEditingModel(true)}>修改配置</button></section>
            : <section className="onboarding-panel" aria-label="准备模型"><h2>先准备模型配置</h2><p>安装时会再次请你同意复制；复制后由常驻助手独立管理。</p>{modelSetup}</section>)}
          <LocalResidentCard onboarding client={localClient} onUse={finish} onStatus={status => { setLocal(status); callbacks.current.onStatus(status); }} onBusyChange={setLocalBusy}
            awaitingModel={!modelReady || editingModel}
            blocked={modelBusy || !local?.installed && (!modelReady || editingModel)} />
          <p className="onboarding-note">安装、启动、连接分别由你操作。初次安装关闭定时作业、心跳与即时通讯；连接后进入个人空间。</p>
        </> : <>
          {profiles.some(isAssistantHost) && <section className="onboarding-panel"><h2>已登记的助手</h2><div className="onboarding-known">{profiles.filter(isAssistantHost).map(p => <button key={p.id} disabled={blocked} onClick={() => connectKnown(p)}>{p.name}{p.assistantPending ? " · 待连接" : ""}<ArrowRight /></button>)}</div></section>}
          <RemoteProjectDialog purpose="assistant" embedded deferSelection onRegistered={finish} onBusyChange={setRemoteBusy} onClose={() => choose(null)} />
        </>}
      {(choice || restoring) && <footer className="onboarding-footer"><button disabled={blocked} onClick={() => choose(null)}><ArrowLeft />重新选择运行位置</button><p>已保存的配置和安装的程序会保留；不会自动移动或合并原资料。</p></footer>}
    </div>
  </dialog>;
}
