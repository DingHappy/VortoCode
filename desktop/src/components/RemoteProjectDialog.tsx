import { invoke } from "@tauri-apps/api/core";
import { useState } from "react";
import { ASSISTANT_HOST_ROOT } from "../lib/assistantHost";
import type { DesktopProjectProfile } from "../types";

/** purpose=assistant：登记常驻电脑（助理跑在服务器的通用范围里，没有项目目录要填）。 */
export function RemoteProjectDialog({ purpose = "project", onClose, onRegistered }: {
  purpose?: "project" | "assistant";
  onClose: () => void;
  onRegistered: (profile: DesktopProjectProfile) => Promise<void>;
}) {
  const [serverUrl, setServerUrl] = useState("");
  const assistant = purpose === "assistant";
  const [repoRoot, setRepoRoot] = useState(assistant ? ASSISTANT_HOST_ROOT : "");
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
  const title = assistant ? "连接常驻电脑" : "连接远端工作区";
  return <div className="modal-backdrop"><section className="settings-modal remote-project-dialog" role="dialog" aria-modal="true" aria-label={title}>
    <h2>{title}</h2>
    <p>{assistant
      ? "让助理住在一台一直开着的服务器上：你交代完就可以合上笔记本，它接着做，回来在「动态」里看结果。服务器上要先跑一个通用范围的 VortoCode 服务。"
      : "查看服务器上的任务、日志与队列，在本地下达任务和处理确认。关闭桌面后，服务器继续执行；TG 只负责通知。"}</p>
    <label>名称<input value={name} onChange={(e) => setName(e.target.value)} placeholder={assistant ? "常驻电脑" : "我的服务器"} disabled={busy} /></label>
    <label>服务地址<input autoFocus value={serverUrl} onChange={(e) => setServerUrl(e.target.value)} placeholder={assistant ? "http://内网主机:8081" : "http://内网主机:8080"} disabled={busy} /></label>
    {!assistant && <label>服务器上的项目目录<input value={repoRoot} onChange={(e) => setRepoRoot(e.target.value)} placeholder="/home/user/project" disabled={busy} /></label>}
    <label>服务访问 Token<input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="VORTOCODE_API_TOKEN（不是 TG Bot Token）" disabled={busy} /></label>
    <p>访问 Token 保存到系统钥匙串。支持内网或 Tailscale 地址；请填写服务实际使用的项目目录。</p>
    {error && <p role="alert">{error}</p>}
    <div className="settings-actions"><button onClick={onClose} disabled={busy}>取消</button><button onClick={() => void connect()} disabled={busy || !serverUrl.trim() || !repoRoot.trim() || !token.trim()}>{busy ? "正在连接…" : "连接"}</button></div>
  </section></div>;
}
