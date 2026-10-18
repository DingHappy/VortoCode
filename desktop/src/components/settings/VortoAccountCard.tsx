// 设置 → 常规 →「VortoCode 账号」：登录 / 退出 / 当前账号与本机设备名。和中转站的「账号」页是两回事。
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useState } from "react";

import { confirmAction } from "../../lib/confirm";
import { errorText } from "../../lib/errorText";
import {
  accountHeadline, cancelAccountLogin, getAccountStatus, loginAccount, logoutAccount, type VortoAccountStatus,
} from "../../lib/vortoAccount";

export function VortoAccountCard() {
  const [status, setStatus] = useState<VortoAccountStatus | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    let alive = true;
    void getAccountStatus().then((value) => { if (alive) setStatus(value); })
      .catch((error) => { if (alive) setNote(errorText(error, "读取登录状态失败")); });
    return () => { alive = false; };
  }, []);

  const login = async () => {
    setLoggingIn(true); setNote("");
    try { setStatus(await loginAccount()); }
    catch (error) { setNote(errorText(error, "登录没有完成")); }
    finally { setLoggingIn(false); }
  };

  const logout = async () => {
    if (!await confirmAction("退出 VortoCode 账号？这台电脑会从账号的设备列表里移除。")) return;
    setBusy(true); setNote("");
    try { setStatus(await logoutAccount()); }
    catch (error) { setNote(errorText(error, "退出失败")); }
    finally { setBusy(false); }
  };

  const signedIn = Boolean(status?.signedIn) && !loggingIn;
  return <section className="settings-card" aria-label="VortoCode 账号">
    <div className="settings-card-head">
      <strong>VortoCode 账号</strong>
      {loggingIn
        ? <button onClick={() => void cancelAccountLogin()}>取消</button>
        : signedIn
          ? <span>
              <button onClick={() => void openUrl(status!.manageUrl)}>管理账号</button>{" "}
              <button onClick={() => void logout()} disabled={busy}>{busy ? "退出中…" : "退出登录"}</button>
            </span>
          : <button className="primary" onClick={() => void login()} disabled={!status}>登录</button>}
    </div>
    <p>{accountHeadline(status, loggingIn)}</p>
    {signedIn && status?.deviceName && <p className="settings-card-note">本机设备名：{status.deviceName}</p>}
    {note && <p role="alert">{note}</p>}
    <p className="settings-card-note">
      {signedIn
        ? "「管理账号」在网页上查看所有登录过的设备、安全记录，修改密码。"
        : "会打开浏览器，在网页上输入密码；Desktop 不经手密码，登录凭证存在 macOS 钥匙串里。目前需要邀请码注册。"}
    </p>
  </section>;
}
