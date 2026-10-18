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
  /** 每期总额；0 = 不知道 */
  creditsPerPeriod: number;
  keyEnabled: boolean;
  inUse: boolean;
}

/** 本期剩余不足一成：提前提醒，免得用到一半停下。 */
export function creditsLow(membership: VortoMembership | null): boolean {
  return Boolean(membership?.member) && membership!.creditsPerPeriod > 0
    && membership!.creditsAvailable < membership!.creditsPerPeriod * 0.1;
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
  const low = creditsLow(membership) ? "（不足一成，用完后要等下期发放）" : "";
  return `会员「${membership.planName}」· 到期 ${day(membership.expiresAt)} · 本期剩余 ${credits} Credit${low}`;
}

/** 卡片上的一句话状态。 */
export function accountHeadline(status: VortoAccountStatus | null, loggingIn: boolean): string {
  if (loggingIn) return "已打开浏览器：在网页上登录并点「允许」";
  if (!status) return "正在读取登录状态…";
  if (!status.signedIn) return status.note || "未登录";
  const who = status.email || "已登录";
  return status.offline ? `${who}（暂时连不上账号服务，稍后会自动恢复）` : who;
}

/** 账号卡片登录、退出、换会员模型后广播一下，左下角据此刷新。 */
export const ACCOUNT_CHANGED_EVENT = "vortocode-account-changed";
export function announceAccountChanged() {
  window.dispatchEvent(new Event(ACCOUNT_CHANGED_EVENT));
}

export interface SidebarIdentity {
  /** 头像字母；空串 = 用默认人像图标 */
  initial: string;
  name: string;
  detail: string;
  title: string;
}

/** 左下角显示谁：VortoCode 账号本人（不是模型服务），会员时带上档位。 */
export function sidebarIdentity(status: VortoAccountStatus | null, membership: VortoMembership | null): SidebarIdentity {
  if (!status) return { initial: "", name: "VortoCode 账号", detail: "", title: "VortoCode 账号" };
  if (!status.signedIn) return { initial: "", name: "登录", detail: "", title: "登录 VortoCode 账号" };
  const name = status.email || "已登录";
  const detail = membership?.member
    ? `${membership.planName}会员${creditsLow(membership) ? " · 额度不足" : ""}`
    : status.offline ? "离线" : "";
  return { initial: name.slice(0, 1).toUpperCase(), name, detail, title: `已登录 ${name}${detail ? ` · ${detail}` : ""}` };
}

/** 账号卡片上的「购买 / 续费」入口：不是会员、或本期额度不足时给。打开账号网站的会员页。 */
export function membershipAction(status: VortoAccountStatus | null, membership: VortoMembership | null): { label: string; url: string } | null {
  if (!status?.signedIn || !membership?.available || !status.manageUrl) return null;
  const url = status.manageUrl.replace(/\/account$/, "/membership");
  if (!membership.member) return { label: "开通会员", url };
  if (creditsLow(membership)) return { label: "续费 / 查看档位", url };
  return null;
}
