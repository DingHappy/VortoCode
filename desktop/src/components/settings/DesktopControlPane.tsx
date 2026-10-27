// 设置 → 桌面操作（实验）：让 Agent 截屏、看窗口，点击/输入/按键/打开应用逐次确认。
// 启用、或开着时换目标机器/应用名单都走原生确认（放权）；保存后要重启当前引擎，工具清单才会变化。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";

import { useNativeSetting } from "../../hooks/useNativeSetting";
import { SettingsReadFeedback } from "./SettingsReadFeedback";
import { desktopControlChange, type DesktopControlIntent } from "../../lib/desktopControlChange";
import { restartSavedSetting } from "../../lib/settingsApply";
import { errorText } from "../../lib/errorText";
import type { DesktopControlStatus } from "../../types";

export function DesktopControlPane({ onRestartRuntime, onDraftStateChange }: { onRestartRuntime: () => Promise<boolean>; onDraftStateChange?: (state: { dirty: boolean; busy: boolean }) => void }) {
  const [target, setTarget] = useState("");
  const [apps, setApps] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const busyRef = useRef(false);
  const setting = useNativeSetting<DesktopControlStatus>("get_desktop_control", "读取桌面操作设置失败", value => {
    setTarget(value.target); setApps(value.apps);
  });
  const status = setting.value;
  const unavailable = setting.loading || Boolean(setting.error) || !status;

  const dirty = !!status && (target.trim() !== status.target || apps.trim() !== status.apps);

  useEffect(() => { onDraftStateChange?.({ dirty, busy }); }, [dirty, busy, onDraftStateChange]);

  const save = async (intent: DesktopControlIntent) => {
    if (busyRef.current || unavailable || !status) return;
    busyRef.current = true;
    setBusy(true);
    setNote("");
    try {
      const change = desktopControlChange(status, { target, apps }, intent);
      const next = await invoke<DesktopControlStatus>("set_desktop_control", change.args);
      setting.replace(next);
      if (!change.keepDraft) { setTarget(next.target); setApps(next.apps); }
      if (!change.restart) {
        setNote("连接配置已保存；桌面操作配置仍为关闭。");
      } else {
        const feedback = await restartSavedSetting(onRestartRuntime, next.enabled ? "桌面操作配置已开启" : "桌面操作配置已关闭");
        setNote(feedback.message + (change.keepDraft && dirty ? "；连接草稿尚未保存。" : ""));
      }
    } catch (failure) {
      setNote(errorText(failure, "修改失败"));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="settings-stack">
      <p className="settings-intro">实验功能：让 Agent 操作一台 Linux 桌面（X11）——截屏、看窗口、点击、输入、按键、打开应用。默认关闭。</p>
      <section className="settings-card" aria-label="桌面操作" aria-busy={setting.loading}>
        <div className="settings-card-head">
          <strong>桌面操作（实验）</strong>
          <button
            className={status?.enabled ? "" : "primary"}
            onClick={() => void save("toggle")}
            disabled={busy || unavailable}
          >{busy ? "处理中…" : unavailable ? "状态待读取" : status?.enabled ? "关闭" : "启用"}</button>
        </div>
        <SettingsReadFeedback label="桌面设置" loading={setting.loading} error={setting.error} onRetry={setting.reload} />
        <div className="custom-provider-form">
          <label><span>目标桌面</span>
            <input aria-label="目标桌面" value={target} disabled={busy || unavailable} maxLength={200} placeholder="ssh:用户@主机（Linux 本机可留空）"
              onChange={(event) => { setTarget(event.target.value); setNote(""); }} />
          </label>
          <label><span>可打开的应用</span>
            <input aria-label="可打开的应用" value={apps} disabled={busy || unavailable} maxLength={400} placeholder="firefox, mousepad（逗号分隔，留空则不能打开应用）"
              onChange={(event) => { setApps(event.target.value); setNote(""); }} />
          </label>
          {dirty && <button onClick={() => void save("save")} disabled={busy || unavailable}>{status?.enabled ? "保存并应用配置" : "保存连接配置"}</button>}
        </div>
        <ul className="settings-card-list">
          <li>目标机器要装 xdotool 和 scrot，并能用 SSH 密钥免密登录（不支持密码登录）；它的图形会话要是 X11，Wayland 不行。</li>
          <li>截屏和列窗口不用确认；点击、输入、按键和打开应用每次都会先问你，即使授权级别是「完全信任」。</li>
          <li>疑似密码或密钥的内容不会被输入；打开应用只限上面的名单。</li>
          <li>只在 Desktop 会话中可用；IM 和自动化任务不会获得这项能力。</li>
        </ul>
        <p className="settings-card-note" role="status">
          {unavailable ? "" : status?.enabled ? `配置已开启 · ${status.target === "local" ? "本机桌面" : status.target.replace(/^ssh:/, "")}` : "配置已关闭"}
          {note ? ` · ${note}` : ""}
        </p>
      </section>
    </div>
  );
}
