// VortoCode 账号（与中转站「账号」页无关）：登录走系统浏览器 + 本机回环回调（Rust 侧 account.rs），
// 令牌只在 Rust 和 macOS Keychain 里，webview 只拿到邮箱、设备名这类展示信息。
import { invoke } from "@tauri-apps/api/core";

import type { DesktopLlmProfileStatus } from "../types";

export interface VortoAccountStatus {
  signedIn: boolean;
  email: string;
  deviceName: string;
  offline: boolean;
  manageUrl: string;
  note: string;
}

export interface VortoMembership {
  available: boolean;
  member: boolean;
  planName: string;
  expiresAt: number;
  creditsAvailable: number;
  keyEnabled: boolean;
  inUse: boolean;
}

/** 模型配置里「这把 Key 来自 VortoCode 会员」的标记（与 Rust 侧 MEMBER_KEY_SOURCE 一致）。 */
export const MEMBER_KEY_SOURCE = "vortocode_member";
export const ACCOUNT_MANAGE_URL = "https://account.vortotech.com/account";

export const getAccountStatus = () => invoke<VortoAccountStatus>("account_status");
export const loginAccount = () => invoke<VortoAccountStatus>("account_login");
export const cancelAccountLogin = () => invoke<void>("account_login_cancel");
export const logoutAccount = () => invoke<VortoAccountStatus>("account_logout");
export const getMembership = () => invoke<VortoMembership>("account_membership");
export const applyMembership = () => invoke<DesktopLlmProfileStatus>("account_use_membership");

/** 当前模型服务的 Key 从哪来，给人看的说法。 */
export function keySourceLabel(keySource: string | undefined): string {
  if (keySource === MEMBER_KEY_SOURCE) return "VortoCode 会员";
  if (keySource === "token_plan") return "Token Plan";
  return "按量 Key";
}

function day(seconds: number): string {
  const date = new Date(seconds * 1000);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** 卡片上的会员一句话；null 表示不显示会员行（服务没开会员功能）。 */
export function membershipLine(membership: VortoMembership | null): string | null {
  if (!membership || !membership.available) return null;
  if (!membership.member) return membership.planName ? `会员「${membership.planName}」已到期` : "还不是会员";
  const credits = membership.creditsAvailable.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
  return `会员「${membership.planName}」· 到期 ${day(membership.expiresAt)} · 本期剩余 ${credits} Credit`;
}

/** 卡片上的一句话状态。 */
export function accountHeadline(status: VortoAccountStatus | null, loggingIn: boolean): string {
  if (loggingIn) return "已打开浏览器：在网页上登录并点「允许」";
  if (!status) return "正在读取登录状态…";
  if (!status.signedIn) return status.note || "未登录";
  const who = status.email || "已登录";
  return status.offline ? `${who}（暂时连不上账号服务，稍后会自动恢复）` : who;
}
