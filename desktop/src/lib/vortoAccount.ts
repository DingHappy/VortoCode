// VortoCode 账号（与中转站「账号」页无关）：登录走系统浏览器 + 本机回环回调（Rust 侧 account.rs），
// 令牌只在 Rust 和 macOS Keychain 里，webview 只拿到邮箱、设备名这类展示信息。
import { invoke } from "@tauri-apps/api/core";

export interface VortoAccountStatus {
  signedIn: boolean;
  email: string;
  deviceName: string;
  offline: boolean;
  manageUrl: string;
  note: string;
}

export const getAccountStatus = () => invoke<VortoAccountStatus>("account_status");
export const loginAccount = () => invoke<VortoAccountStatus>("account_login");
export const cancelAccountLogin = () => invoke<void>("account_login_cancel");
export const logoutAccount = () => invoke<VortoAccountStatus>("account_logout");

/** 卡片上的一句话状态。 */
export function accountHeadline(status: VortoAccountStatus | null, loggingIn: boolean): string {
  if (loggingIn) return "已打开浏览器：在网页上登录并点「允许」";
  if (!status) return "正在读取登录状态…";
  if (!status.signedIn) return status.note || "未登录";
  const who = status.email || "已登录";
  return status.offline ? `${who}（暂时连不上账号服务，稍后会自动恢复）` : who;
}
