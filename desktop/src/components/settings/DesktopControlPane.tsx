// 设置 → 桌面操作（实验）：让 Agent 截屏、看窗口，点击/输入/按键/打开应用逐次确认。
// 启用、或开着时换目标机器/应用名单都走原生确认（放权）；保存后要重启当前引擎，工具清单才会变化。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";

import { errorText } from "../../lib/errorText";
import type { DesktopControlStatus } from "../../types";

export function DesktopControlPane({ onRestartRuntime }: { onRestartRuntime: () => Promise<boolean> }) {
  const [status, setStatus] = useState<DesktopControlStatus | null>(null);
  const [target, setTarget] = useState("");
  const [apps, setApps] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    invoke<DesktopControlStatus>("get_desktop_control")
      .then((value) => { if (!alive) return; setStatus(value); setTarget(value.target); setApps(value.apps); })
      .catch((failure) => { if (alive) setNote(errorText(failure, "读取桌面操作设置失败")); });
    return () => { alive = false; };
  }, []);

  const dirty = !!status && (target.trim() !== status.target || apps.trim() !== status.apps);

  const save = async (enabled: boolean) => {
    setBusy(true);
    setNote("");
    try {
      const next = await invoke<DesktopControlStatus>("set_desktop_control", { enabled, target, apps });
      setStatus(next);
      setTarget(next.target);
      setApps(next.apps);
      const restarted = await onRestartRuntime();
      setNote(restarted ? (next.enabled ? "已启用，当前引擎已重启" : "已关闭，当前引擎已重启") : "已保存，下次启动引擎时生效");
    } catch (failure) {
      setNote(errorText(failure, "修改失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-stack">
      <p className="settings-intro">实验功能：让 Agent 操作一台 Linux 桌面（X11）——截屏、看窗口、点击、输入、按键、打开应用。默认关闭。</p>
      <section className="settings-card" aria-label="桌面操作">
        <div className="settings-card-head">
          <strong>桌面操作（实验）</strong>
          <button
            className={status?.enabled ? "" : "primary"}
            onClick={() => void save(!status?.enabled)}
            disabled={busy || !status}
          >{busy ? "处理中…" : status?.enabled ? "关闭" : "启用"}</button>
        </div>
        <div className="custom-provider-form">
          <label><span>目标桌面</span>
            <input aria-label="目标桌面" value={target} disabled={busy} maxLength={200} placeholder="ssh:用户@主机（Linux 本机可留空）"
              onChange={(event) => setTarget(event.target.value)} />
          </label>
          <label><span>可打开的应用</span>
            <input aria-label="可打开的应用" value={apps} disabled={busy} maxLength={400} placeholder="firefox, mousepad（逗号分隔，留空则不能打开应用）"
              onChange={(event) => setApps(event.target.value)} />
          </label>
          {status?.enabled && dirty && <button onClick={() => void save(true)} disabled={busy}>保存并重启引擎</button>}
        </div>
        <ul className="settings-card-list">
          <li>目标机器要装 xdotool 和 scrot，并能用 SSH 密钥免密登录（不支持密码登录）；它的图形会话要是 X11，Wayland 不行。</li>
          <li>截屏和列窗口不用确认；点击、输入、按键和打开应用每次都会先问你，即使授权级别是「完全信任」。</li>
          <li>疑似密码或密钥的内容不会被输入；打开应用只限上面的名单。</li>
          <li>只在 Desktop 会话中可用；IM 和自动化任务不会获得这项能力。</li>
        </ul>
        <p className="settings-card-note">
          {status === null ? "读取中…" : status.enabled ? `已启用 · ${status.target === "local" ? "本机桌面" : status.target.slice(4)}` : "未启用"}
          {note ? ` · ${note}` : ""}
        </p>
      </section>
    </div>
  );
}
