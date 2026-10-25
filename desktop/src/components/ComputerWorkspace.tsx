import { isTauri } from "@tauri-apps/api/core";
import { ArrowUpRight, Monitor, Settings2, X } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";
import { desktopConnectionLabel, residentDesktop, watchResidentDesktop, type ResidentDesktopStatus } from "../lib/residentDesktop";
import type { ComputerWorkspace as Workspace } from "../hooks/useComputerWorkspace";
import "./ComputerWorkspace.css";

export function ComputerLauncherView({ status, busy, onOpen, onSettings }: {
  status: ResidentDesktopStatus | null; busy: boolean; onOpen: () => void; onSettings: () => void;
}) {
  return <div className="computer-launcher"><button className="computer-launcher-main" onClick={status?.target ? onOpen : onSettings} disabled={busy}>
    <Monitor size={22} aria-hidden="true" /><span><strong>{status?.target.replace(/^ssh:/, "") || "连接你的电脑"}</strong>
      <small>{busy ? "正在连接…" : desktopConnectionLabel(status, Date.now())} · {status?.target ? "打开电脑" : "设置连接"}</small></span></button>
    <button className="computer-launcher-settings" aria-label="电脑连接设置" onClick={onSettings}><Settings2 size={15} /></button></div>;
}
export function ComputerLauncher(props: { onOpen: () => void; onSettings: () => void }) {
  const { status, busy } = useSyncExternalStore(residentDesktop.subscribe, residentDesktop.getSnapshot);
  useEffect(() => { if (isTauri()) return watchResidentDesktop(); }, []);
  return isTauri() ? <ComputerLauncherView status={status} busy={busy} {...props} /> : null;
}

export function ComputerWorkspaceView({ view, live = true, onClose, onRetry, onWindow }: {
  view: Workspace; live?: boolean; onClose: () => void; onRetry: () => void; onWindow: () => void;
}) {
  return <aside className="computer-workspace" aria-label="电脑工作区">
    <header><div><Monitor size={16} /><strong>{view.target.replace(/^ssh:/, "")}</strong><span>电脑</span></div>
      <button aria-label="在独立窗口打开电脑" title="在独立窗口打开" onClick={onWindow} disabled={!view.url || !live}><ArrowUpRight size={16} /></button>
      <button aria-label="关闭电脑工作区" onClick={onClose}><X size={17} /></button></header>
    <p className="computer-workspace-hint">默认仅观察；需要操作时，在画面里确认接管。</p>
    {view.url && live ? <iframe src={view.url} title={`远程电脑 ${view.target.replace(/^ssh:/, "")}`}
      sandbox="allow-scripts allow-same-origin" allow="fullscreen" referrerPolicy="no-referrer" />
      : <div className="computer-workspace-empty"><Monitor size={34} /><strong>{view.error ? "暂时无法连接" : !live ? "画面连接已结束" : "正在连接你的电脑…"}</strong>
        <p>{view.error || (!live ? "连接可能已断开或切换到其他窗口。聊天仍可继续。" : "聊天保持在左侧，连接成功后这里会显示画面。")}</p>
        {(view.error || !live) && <button onClick={onRetry}>重新连接</button>}</div>}
  </aside>;
}
export function ComputerWorkspacePanel(props: { view: Workspace; onClose: () => void; onRetry: () => void }) {
  const { status } = useSyncExternalStore(residentDesktop.subscribe, residentDesktop.getSnapshot);
  useEffect(watchResidentDesktop, []);
  const live = props.view.id === undefined || status?.viewer?.id === props.view.id && status.viewer.surface === "workspace";
  return <ComputerWorkspaceView {...props} live={live} onWindow={() => {
    void residentDesktop.act("open_resident_desktop").then(next => { if (next?.viewer?.surface === "window") props.onClose(); });
  }} />;
}
