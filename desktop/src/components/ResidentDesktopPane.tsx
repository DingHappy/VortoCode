import { isTauri } from "@tauri-apps/api/core";
import { useEffect, useState, useSyncExternalStore } from "react";
import { desktopConnectionLabel, residentDesktop, watchResidentDesktop } from "../lib/residentDesktop";
import "./ResidentDesktopPane.css";

export function ResidentDesktopPane({ compact = false }: { compact?: boolean }) {
  return isTauri() ? <DesktopPane compact={compact} /> : null;
}

function DesktopPane({ compact }: { compact: boolean }) {
  const { status, busy, note } = useSyncExternalStore(residentDesktop.subscribe, residentDesktop.getSnapshot);
  const [target, setTarget] = useState("");
  const [editing, setEditing] = useState(false);
  const [now, setNow] = useState(Date.now);
  useEffect(watchResidentDesktop, []);
  useEffect(() => { setNow(Date.now()); }, [status?.checkedAt]);
  useEffect(() => { if (!editing) setTarget(status?.target.replace(/^ssh:/, "") ?? ""); }, [status?.target, editing]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 10_000); return () => clearInterval(timer); }, []);
  const dirty = target.trim().replace(/^ssh:/, "") !== (status?.target.replace(/^ssh:/, "") ?? "");
  const configure = !compact || editing || (status !== null && !status.target);
  const run = (command: Parameters<typeof residentDesktop.act>[0]) => { void residentDesktop.act(command); };
  const current = !!status?.sshConnected && !status.error && status.checkedAt !== null
    && now >= status.checkedAt && now - status.checkedAt < 60_000;
  return <section className={`resident-desktop-pane ${compact ? "compact" : "settings-card"}`} aria-label="远程桌面连接">
    <div className="resident-desktop-heading"><strong>远程桌面</strong>
      {compact && <button disabled={busy} onClick={() => setEditing(!editing)}>{editing ? "收起设置" : "连接设置"}</button>}</div>
    <p>{status?.target ? `${status.target.replace(/^ssh:/, "")} · ` : ""}{desktopConnectionLabel(status, now)}</p>
    {status?.checkedAt !== null && status?.checkedAt !== undefined && <p className="resident-desktop-note">最近核对：{new Date(status.checkedAt).toLocaleTimeString()}</p>}
    {configure && <form onSubmit={event => { event.preventDefault(); void residentDesktop.act("save_resident_desktop_target", { target }); }}>
      <label>远程电脑（SSH）<input aria-label="远程电脑 SSH 目标" value={target} maxLength={200} disabled={busy || !status}
        autoCapitalize="none" autoCorrect="off" autoComplete="off" spellCheck={false}
        placeholder="例如 vorto-code 或 用户@主机" onChange={event => { setEditing(true); setTarget(event.target.value); }} /></label>
      <button disabled={busy || !status || !target.trim() || !dirty}>保存目标</button>
    </form>}
    <div className="resident-desktop-actions">
      <button disabled={busy || !status?.target || dirty} onClick={() => run("open_resident_desktop")}>{busy ? "处理中…" : status?.viewerUrl ? "显示远程画面" : "打开远程画面"}</button>
      <button disabled={busy || !status?.target || dirty} onClick={() => run("check_resident_desktop")}>检查连接</button>
      {status?.sshConnected && <button disabled={busy} onClick={() => run("close_resident_desktop")}>断开连接</button>}
    </div>
    {current && status.computer && <p>密码库：{status.computer.passwordStore === "unlocked" ? "已解锁" : status.computer.passwordStore === "locked" ? "需要你解锁" : "待检查"}
      {status.computer.observers === null ? "" : ` · ${status.computer.observers} 个观察连接`} · {status.computer.viewers} 个接管连接</p>}
    {note && <p role="status">{note}</p>}
    {status?.error && status.error !== note && <p role="status">{status.error}</p>}
    <p className="resident-desktop-note">默认仅观察，可在画面窗口切换接管。SSH 需已配置密钥与可信主机；关闭窗口会断开本客户端的隧道，远端继续运行。
      连接期间自动核对状态，断线后请手动重连。此连接独立于当前对话，不会启用助手桌面操作。</p>
  </section>;
}
