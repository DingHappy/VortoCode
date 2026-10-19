// 设置 → 常规 →「VortoCode 账号」：登录 / 退出 / 当前账号与本机设备名，以及会员模型。
// 和中转站的「账号」页是两回事；会员 Key 只在 Rust 和配置文件里，界面只看到套餐名和额度。
import { openUrl } from "@tauri-apps/plugin-opener";
import { useCallback, useEffect, useState } from "react";

import { confirmAction } from "../../lib/confirm";
import { errorText } from "../../lib/errorText";
import { feedbackMailto } from "../../lib/feedback";
import {
  accountHeadline, announceAccountChanged, applyMembership, cancelAccountLogin, getAccountStatus, getMembership, loginAccount, logoutAccount,
  membershipAction, membershipLine, type VortoAccountStatus, type VortoMembership,
} from "../../lib/vortoAccount";
import type { DesktopLlmProfileStatus } from "../../types";

interface Props {
  /** 本机是否已配好模型服务：没配时，会员登录后直接问要不要用会员模型开始 */
  modelConfigured: boolean;
  onProfile: (profile: DesktopLlmProfileStatus) => void;
  onRestartRuntime: () => Promise<boolean>;
  onProfileCleared: () => Promise<void>;
}

export function VortoAccountCard({ modelConfigured, onProfile, onRestartRuntime, onProfileCleared }: Props) {
  const [status, setStatus] = useState<VortoAccountStatus | null>(null);
  const [membership, setMembership] = useState<VortoMembership | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const loadMembership = useCallback(async (current: VortoAccountStatus | null): Promise<VortoMembership | null> => {
    let value: VortoMembership | null = null;
    if (current?.signedIn && !current.offline) {
      try { value = await getMembership(); }
      catch { value = null; }     // 读不到会员信息不影响账号本身
    }
    setMembership(value);
    return value;
  }, []);

  useEffect(() => {
    let alive = true;
    void getAccountStatus().then((value) => {
      if (!alive) return;
      setStatus(value);
      void loadMembership(value);
    }).catch((error) => { if (alive) setNote(errorText(error, "读取登录状态失败")); });
    return () => { alive = false; };
  }, [loadMembership]);

  const login = async () => {
    setLoggingIn(true); setNote("");
    try {
      const value = await loginAccount();
      setStatus(value);
      const member = await loadMembership(value);
      announceAccountChanged();
      setLoggingIn(false);
      if (member?.member && member.keyEnabled && !member.inUse && !modelConfigured) {
        await useMember("你是 VortoCode 会员。现在就用会员模型开始吗？不用再填服务地址和 Key。");
      }
    } catch (error) { setNote(errorText(error, "登录没有完成")); }
    finally { setLoggingIn(false); }
  };

  const logout = async () => {
    const message = membership?.inUse || membership?.listed
      ? "退出 VortoCode 账号？这台电脑会从账号的设备列表里移除，会员模型 Key 也会从本机删除。"
      : "退出 VortoCode 账号？这台电脑会从账号的设备列表里移除。";
    if (!await confirmAction(message)) return;
    setBusy(true); setNote("");
    const wasInUse = Boolean(membership?.inUse || membership?.listed);
    try {
      setStatus(await logoutAccount());
      setMembership(null);
      announceAccountChanged();
      if (wasInUse) { await onProfileCleared(); await onRestartRuntime(); }
    } catch (error) { setNote(errorText(error, "退出失败")); }
    finally { setBusy(false); }
  };

  // 已经配了别的默认模型（本机模型、自己的 Key）时，会员模型是「加进模型列表」，默认的不动
  const useMember = async (question = modelConfigured
    ? "把会员模型加进模型列表？现在的默认模型不变，在输入框的模型菜单里随时切换。"
    : "用 VortoCode 会员模型作为默认模型服务？") => {
    if (!await confirmAction(question)) return;
    setBusy(true); setNote("");
    try {
      onProfile(await applyMembership());
      await onRestartRuntime();
      setNote(modelConfigured ? "已加入模型列表：在输入框的模型菜单里选择" : "已切换到会员模型");
      await loadMembership(await getAccountStatus().catch(() => status));   // 刚登录时闭包里的 status 还是旧的
      announceAccountChanged();
    } catch (error) { setNote(errorText(error, "切换失败")); }
    finally { setBusy(false); }
  };

  const signedIn = Boolean(status?.signedIn) && !loggingIn;
  const line = signedIn ? membershipLine(membership) : null;
  const action = signedIn ? membershipAction(status, membership) : null;
  return <section className="settings-card" aria-label="VortoCode 账号">
    <div className="settings-card-head">
      <strong>VortoCode 账号</strong>
      {loggingIn
        ? <button onClick={() => void cancelAccountLogin()}>取消</button>
        : signedIn
          ? <span>
              <button onClick={() => void openUrl(status!.manageUrl)}>管理账号</button>{" "}
              <button onClick={() => void logout()} disabled={busy}>{busy ? "处理中…" : "退出登录"}</button>
            </span>
          : <button className="primary" onClick={() => void login()} disabled={!status}>登录</button>}
    </div>
    <p>{accountHeadline(status, loggingIn)}</p>
    {signedIn && status?.deviceName && <p className="settings-card-note">本机设备名：{status.deviceName}</p>}
    {line && <p>
      {line}
      {membership?.member && (membership.inUse
        ? <span className="settings-card-note">（正在使用）</span>
        : membership.listed
          ? <span className="settings-card-note">（已在模型列表，在输入框的模型菜单里选择）</span>
          : membership.keyEnabled
            ? <>{" "}<button className="primary" onClick={() => void useMember()} disabled={busy}>{modelConfigured ? "加入模型列表" : "使用会员模型"}</button></>
            : <span className="settings-card-note">（会员模型 Key 已停用）</span>)}
    </p>}
    <div className="settings-card-actions">
      {action && <button onClick={() => void openUrl(action.url)}>{action.label}</button>}
      <button onClick={() => void openUrl(feedbackMailto())} title="用邮件告诉我们遇到的问题">问题反馈</button>
    </div>
    {note && <p role="alert">{note}</p>}
    <p className="settings-card-note">
      {signedIn
        ? "「管理账号」在网页上查看所有登录过的设备、安全记录、会员，修改密码。"
        : "会打开浏览器，在网页上输入密码；Desktop 不经手密码，登录凭证存在 macOS 钥匙串里。目前需要邀请码注册。"}
    </p>
  </section>;
}
