import { invoke } from "@tauri-apps/api/core";
import { useState } from "react";
import type { DesktopProjectProfile } from "../types";

export function RemoteProjectDialog({ onClose, onRegistered }: {
  onClose: () => void;
  onRegistered: (profile: DesktopProjectProfile) => Promise<void>;
}) {
  const [serverUrl, setServerUrl] = useState("");
  const [repoRoot, setRepoRoot] = useState("");
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function connect() {
    setBusy(true); setError("");
    try {
      const profile = await invoke<DesktopProjectProfile>("remember_remote_project", {
        serverUrl: serverUrl.trim(), remoteRepoRoot: repoRoot.trim(), name: name.trim(), token: token.trim(),
      });
      setToken("");
      await onRegistered(profile);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setBusy(false);
    }
  }
  return <div className="modal-backdrop"><section className="settings-modal remote-project-dialog" role="dialog" aria-modal="true" aria-label="连接远端工作区">
    <h2>连接远端工作区</h2>
    <p>查看服务器上的任务、日志与队列，在本地下达任务和处理确认。关闭桌面后，服务器继续执行；TG 只负责通知。</p>
    <label>名称<input value={name} onChange={(e) => setName(e.target.value)} placeholder="我的服务器" disabled={busy} /></label>
    <label>服务地址<input autoFocus value={serverUrl} onChange={(e) => setServerUrl(e.target.value)} placeholder="http://内网主机:8080" disabled={busy} /></label>
    <label>服务器上的项目目录<input value={repoRoot} onChange={(e) => setRepoRoot(e.target.value)} placeholder="/home/user/project" disabled={busy} /></label>
    <label>服务访问 Token<input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="VORTOCODE_API_TOKEN（不是 TG Bot Token）" disabled={busy} /></label>
    <p>访问 Token 保存到系统钥匙串。支持内网或 Tailscale 地址；请填写服务实际使用的项目目录。</p>
    {error && <p role="alert">{error}</p>}
    <div className="settings-actions"><button onClick={onClose} disabled={busy}>取消</button><button onClick={() => void connect()} disabled={busy || !serverUrl.trim() || !repoRoot.trim() || !token.trim()}>{busy ? "正在连接…" : "连接"}</button></div>
  </section></div>;
}
