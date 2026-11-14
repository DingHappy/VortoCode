import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";
import { ASSISTANT_HOST_ROOT } from "../lib/assistantHost";
import type { DesktopProjectProfile } from "../types";

/** purpose=assistant：登记常驻电脑（助理跑在服务器的通用范围里，没有项目目录要填）。 */
export function RemoteProjectDialog({ purpose = "project", onClose, onRegistered, embedded = false, deferSelection = false, onBusyChange }: {
  purpose?: "project" | "assistant";
  onClose: () => void;
  onRegistered: (profile: DesktopProjectProfile) => Promise<void>;
  embedded?: boolean;
  deferSelection?: boolean;
  onBusyChange?: (busy: boolean) => void;
}) {
  const alive = useRef(true);
  const running = useRef(false);
  const busyCallback = useRef(onBusyChange); busyCallback.current = onBusyChange;
  useEffect(() => { alive.current = true; return () => { alive.current = false; busyCallback.current?.(false); }; }, []);
  const [serverUrl, setServerUrl] = useState("");
  const assistant = purpose === "assistant";
  const [repoRoot, setRepoRoot] = useState(assistant ? ASSISTANT_HOST_ROOT : "");
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [prepared, setPrepared] = useState<DesktopProjectProfile | null>(null);
  async function connect() {
    if (running.current) return;
    running.current = true; busyCallback.current?.(true);
    setBusy(true); setError("");
    try {
      if (deferSelection && prepared && !token.trim()) {
        await onRegistered(prepared);
        return;
      }
      const profile = await invoke<DesktopProjectProfile>("remember_remote_project", {
        serverUrl: serverUrl.trim(), remoteRepoRoot: repoRoot.trim(), name: name.trim(), token: token.trim(),
        ...(deferSelection ? { deferSelection: true } : {}),
      });
      if (!alive.current) return;
      if (deferSelection) setPrepared(profile);
      setToken("");
      await onRegistered(profile);
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      running.current = false;
      if (alive.current) { setBusy(false); busyCallback.current?.(false); }
    }
  }
  const title = assistant ? "连接常驻电脑" : "连接远端工作区";
  const content = <section className={embedded ? "onboarding-remote" : "settings-modal remote-project-dialog"} role={embedded ? undefined : "dialog"} aria-modal={embedded ? undefined : true} aria-label={title}>
    {!embedded && <><h2>{title}</h2>
    <p>{assistant
      ? "让助理住在一台一直开着的服务器上：你交代完就可以合上笔记本，它接着做，回来在「动态」里看结果。服务器上要先跑一个通用范围的 VortoOne 服务。"
      : "查看服务器上的任务、日志与队列，在本地下达任务和处理确认。关闭桌面后，服务器继续执行；TG 只负责通知。"}</p></>}
    <label>名称<input value={name} onChange={(e) => { setPrepared(null); setName(e.target.value); }} placeholder={assistant ? "常驻电脑" : "我的服务器"} disabled={busy} /></label>
    <label>服务地址<input autoFocus value={serverUrl} onChange={(e) => { setPrepared(null); setServerUrl(e.target.value); }} placeholder={assistant ? "http://内网主机:8081" : "http://内网主机:8080"} disabled={busy} /></label>
    {!assistant && <label>服务器上的项目目录<input value={repoRoot} onChange={(e) => setRepoRoot(e.target.value)} placeholder="/home/user/project" disabled={busy} /></label>}
    <label>{embedded ? "主人访问令牌" : "服务访问 Token"}<input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={embedded ? "连接这份助手的访问令牌，不是模型 API Key" : "VORTOONE_API_TOKEN（不是 TG Bot Token）"} disabled={busy} /></label>
    <p>访问 Token 保存到系统钥匙串。{assistant ? `${deferSelection ? "连接成功后才选择这份助手；" : ""}不会复制这台 Mac 的模型配置。` : "支持内网或 Tailscale 地址；请填写服务实际使用的项目目录。"}</p>
    {error && <p role="alert">{error}</p>}
    <div className="settings-actions"><button onClick={onClose} disabled={busy}>取消</button><button onClick={() => void connect()} disabled={busy || !serverUrl.trim() || !repoRoot.trim() || !token.trim() && !prepared}>{busy ? "正在连接…" : prepared ? "重试连接" : "连接"}</button></div>
  </section>;
  return embedded ? content : <div className="modal-backdrop">{content}</div>;
}
