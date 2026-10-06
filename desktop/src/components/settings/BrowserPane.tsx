// 设置 → 浏览器操控：让 Agent 在独立浏览器里打开网页、读取和截图，点击/输入逐次确认。
// 打开走原生确认（放权），关闭直接生效；两者都要重启当前引擎，工具清单才会变化。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";

import { errorText } from "../../lib/errorText";
import type { BrowserControlStatus } from "../../types";

export function BrowserPane({ onRestartRuntime }: { onRestartRuntime: () => Promise<boolean> }) {
  const [status, setStatus] = useState<BrowserControlStatus | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    invoke<BrowserControlStatus>("get_browser_control")
      .then((value) => { if (alive) setStatus(value); })
      .catch((failure) => { if (alive) setNote(errorText(failure, "读取浏览器操控设置失败")); });
    return () => { alive = false; };
  }, []);

  const toggle = async () => {
    if (!status) return;
    setBusy(true);
    setNote("");
    try {
      const next = await invoke<BrowserControlStatus>("set_browser_control", { enabled: !status.enabled });
      setStatus(next);
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
      <p className="settings-intro">让 Agent 在一个独立的浏览器窗口里打开网页、读取内容和截图，处理需要交互或要靠脚本渲染的页面。</p>
      <section className="settings-card" aria-label="浏览器操控">
        <div className="settings-card-head">
          <strong>浏览器操控</strong>
          <button
            className={status?.enabled ? "" : "primary"}
            onClick={() => void toggle()}
            disabled={busy || !status || (!status.enabled && !status.browserPath)}
          >{busy ? "处理中…" : status?.enabled ? "关闭" : "启用"}</button>
        </div>
        <ul className="settings-card-list">
          <li>使用独立的浏览器配置，不含你日常浏览器的登录信息、Cookie 和密码；需要登录的网站请在那个窗口里自己登录。</li>
          <li>每次点击或输入都会先问你，即使授权级别是「完全信任」；不会替你填写密码框。</li>
          <li>支付、银行、邮箱、账号中心、密码管理和云控制台等站点，以及内网地址，会被拦截。</li>
          <li>只在 Desktop 会话中可用；IM 和自动化任务不会获得这项能力。</li>
        </ul>
        <p className="settings-card-note">
          {status === null ? "读取中…" : status.browserPath ? `浏览器：${status.browserPath}` : "没有找到 Chrome / Edge / Chromium / Brave，安装其中之一后才能启用。"}
          {note ? ` · ${note}` : ""}
        </p>
      </section>
    </div>
  );
}
