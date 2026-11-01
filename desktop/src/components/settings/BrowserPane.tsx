// 设置 → 浏览器操控：让 Agent 在独立浏览器里打开网页、读取和截图，点击/输入逐次确认。
// 打开走原生确认（放权），关闭直接生效；两者都要重启当前引擎，工具清单才会变化。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";

import { useNativeSetting } from "../../hooks/useNativeSetting";
import { SettingsReadFeedback } from "./SettingsReadFeedback";
import { restartSavedSetting } from "../../lib/settingsApply";
import { errorText } from "../../lib/errorText";
import type { BrowserControlStatus } from "../../types";

export function BrowserPane({ onRestartRuntime, onDraftStateChange }: { onRestartRuntime: () => Promise<boolean>; onDraftStateChange?: (state: { dirty: boolean; busy: boolean }) => void }) {
  const setting = useNativeSetting<BrowserControlStatus>("get_browser_control", "读取浏览器操作设置失败");
  const status = setting.value;
  const unavailable = setting.loading || Boolean(setting.error) || !status;
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const busyRef = useRef(false);

  useEffect(() => { onDraftStateChange?.({ dirty: false, busy }); }, [busy, onDraftStateChange]);

  const toggle = async () => {
    if (busyRef.current || unavailable || !status) return;
    busyRef.current = true;
    setBusy(true);
    setNote("");
    try {
      const next = await invoke<BrowserControlStatus>("set_browser_control", { enabled: !status.enabled });
      setting.replace(next);
      const feedback = await restartSavedSetting(onRestartRuntime, next.enabled ? "浏览器操作配置已开启" : "浏览器操作配置已关闭");
      setNote(feedback.message);
    } catch (failure) {
      setNote(errorText(failure, "修改失败"));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="settings-stack">
      <p className="settings-intro">让 Agent 在一个独立的浏览器窗口里打开网页、读取内容和截图，处理需要交互或要靠脚本渲染的页面。</p>
      <section className="settings-card" aria-label="浏览器操控" aria-busy={setting.loading}>
        <div className="settings-card-head">
          <strong>浏览器操控</strong>
          <button
            className={status?.enabled ? "" : "primary"}
            onClick={() => void toggle()}
            disabled={busy || unavailable || (!status?.enabled && !status?.browserPath)}
          >{busy ? "处理中…" : unavailable ? "状态待读取" : status?.enabled ? "关闭" : "启用"}</button>
        </div>
        <SettingsReadFeedback label="浏览器设置" loading={setting.loading} error={setting.error} onRetry={setting.reload} />
        {!unavailable && <p className="settings-card-note">配置：{status?.enabled ? "已开启" : "已关闭"}。工具清单在本机引擎重启后更新。</p>}
        <ul className="settings-card-list">
          <li>网站与账号经你确认后，使用各自独立的本机登录目录；不会导入日常浏览器的 Cookie 或密码。撤销助手连接不会退出网站。</li>
          <li>登录、扫码和验证码由你在专用窗口完成，确认恢复前助手暂停读取和截图。每次点击、输入或滚动仍会先问你，即使是「完全信任」。</li>
          <li>可选手机网页模式，模拟页面尺寸和触摸操作；原生 App 控制需单独配置与授权，此开关只控制网页。</li>
          <li>支付、银行、邮箱、账号中心、密码管理和云控制台等站点，以及内网地址，会被拦截。</li>
          <li>只在 Desktop 会话中可用；IM 和自动化任务不会获得这项能力。</li>
        </ul>
        <p className="settings-card-note" role="status">
          {unavailable ? "" : status.browserPath ? `浏览器：${status.browserPath}` : "没有找到 Chrome / Edge / Chromium / Brave，安装其中之一后才能启用。"}
          {note ? ` · ${note}` : ""}
        </p>
      </section>
    </div>
  );
}
