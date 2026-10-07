// 设置 → 账号：用 VortoCode 中转站账号登录，取用账号的 Token Plan（即 Desktop 套餐）。
// 密码只在这次调用里交给原生层，用完即清空；原生层也不落盘、登录会话用完就登出。
// 没有套餐时由用户决定：去购买，或者先用一把按量 Key。也可以不登录，直接在「模型」里填 Key 或加第三方模型。
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useState } from "react";

import { confirmAction } from "../../lib/confirm";
import { errorText } from "../../lib/errorText";
import type { DesktopLlmProfileStatus, RelayLoginOutcome } from "../../types";

const REGISTER_URL = "https://token.vortotech.com/register";
const PLAN_URL = "https://token.vortotech.com/panel/plan";

type Props = {
  profile: DesktopLlmProfileStatus | null;
  onProfile: (profile: DesktopLlmProfileStatus) => void;
  onRestartRuntime: () => Promise<boolean>;
  onOpenModelSettings: () => void;
};

function formatExpiry(seconds?: number | null): string {
  if (!seconds) return "";
  const date = new Date(seconds * 1000);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} 到期`;
}

export function AccountPane({ profile, onProfile, onRestartRuntime, onOpenModelSettings }: Props) {
  const account = profile?.account;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [needsPlan, setNeedsPlan] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const open = (url: string) => void openUrl(url).catch((error) => setNote(errorText(error, "无法打开浏览器")));

  const login = async (allowPayAsYouGo: boolean) => {
    setBusy(true);
    setNote("");
    try {
      const outcome = await invoke<RelayLoginOutcome>("relay_login", { username, password, allowPayAsYouGo });
      if (outcome.needsPlan || !outcome.status) {
        setNeedsPlan(true);
        setNote(outcome.message);
        return;
      }
      setPassword("");
      setNeedsPlan(false);
      onProfile(outcome.status);
      const restarted = await onRestartRuntime();
      setNote(restarted ? `${outcome.message}，当前引擎已重启` : outcome.message);
    } catch (error) {
      setNote(errorText(error, "登录失败"));
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    if (!await confirmAction("退出登录？本机保存的中转站 Key 会被删除；自定义供应商保留。")) return;
    setBusy(true);
    setNote("");
    try {
      onProfile(await invoke<DesktopLlmProfileStatus>("relay_logout"));
      await onRestartRuntime();
      setNote("已退出登录");
    } catch (error) {
      setNote(errorText(error, "退出登录失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-stack">
      <p className="settings-intro">登录 VortoCode 账号即可使用你的 Token Plan（Desktop 套餐），不用手动复制 Key。也可以不登录，直接在「模型」里填写 API Key / Token Plan Key，或添加第三方模型。</p>

      {account ? (
        <section className="settings-card account-card" aria-label="当前账号">
          <div className="settings-card-head">
            <div className="account-identity">
              <span className="account-avatar">{(account.displayName || account.username).slice(0, 1).toUpperCase()}</span>
              <span><strong>{account.displayName || account.username}</strong><small>{account.username}</small></span>
            </div>
            <button onClick={() => void logout()} disabled={busy}>退出登录</button>
          </div>
          <dl className="account-facts">
            <div><dt>使用中</dt><dd>{account.keySource === "token_plan" ? "Token Plan（Desktop 套餐）" : "按量 Key（VortoCode Desktop）"}</dd></div>
            {account.planName && <div><dt>套餐</dt><dd>{account.planName}{account.planExpiry ? ` · ${formatExpiry(account.planExpiry)}` : ""}</dd></div>}
          </dl>
          <div className="settings-card-foot">
            <span>{note}</span>
            <button onClick={() => open(PLAN_URL)}>{account.keySource === "token_plan" ? "管理套餐" : "购买套餐"}</button>
          </div>
        </section>
      ) : (
        <section className="settings-card account-card" aria-label="登录">
          <div className="settings-card-head"><strong>登录 VortoCode</strong></div>
          <form
            className="account-form"
            onSubmit={(event) => { event.preventDefault(); void login(false); }}
          >
            <label><span>用户名</span><input value={username} autoComplete="username" autoCapitalize="off" autoCorrect="off" spellCheck={false} onChange={(event) => { setUsername(event.target.value); setNeedsPlan(false); }} /></label>
            <label><span>密码</span><input type="password" value={password} autoComplete="current-password" onChange={(event) => { setPassword(event.target.value); setNeedsPlan(false); }} /></label>
            <div className="settings-card-foot">
              <span>{note}</span>
              <button type="button" onClick={() => open(REGISTER_URL)}>注册账号</button>
              <button type="submit" className="primary" disabled={busy || !username.trim() || !password}>{busy ? "登录中…" : "登录"}</button>
            </div>
          </form>
          {needsPlan && (
            <div className="account-plan-choice">
              <p>这个账号还没有 Token Plan（Desktop 套餐）。可以先购买套餐，买好后再点「登录」；或者先用按量计费，从账户余额扣费。</p>
              <div>
                <button className="primary" onClick={() => open(PLAN_URL)}>购买套餐</button>
                <button onClick={() => void login(true)} disabled={busy || !password}>先用按量 Key</button>
              </div>
            </div>
          )}
        </section>
      )}

      <button className="account-alt" onClick={onOpenModelSettings}>直接填写 API Key 或添加第三方模型 →</button>
    </div>
  );
}
