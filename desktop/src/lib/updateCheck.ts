// 检查更新：问 GitHub 最新正式版（Rust 侧 check_for_update），有新版就提示。
// 自动检查每天最多一次，只比版本号，可在设置里关掉（关了就完全不联系 GitHub）。
// 安装只在用户点了「更新并重启」后发生：Rust 侧弹原生确认，下载后用内置公钥验签，通过才替换并重启。
import { invoke } from "@tauri-apps/api/core";

import { STORAGE_KEYS } from "./storage";

export interface UpdateCheck {
  current: string;
  latest: string;
  newer: boolean;
  url: string;
  publishedAt: string;
}

export const AUTO_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000;

/** 距上次自动检查是否已满一天；读不到记录就算到期。 */
export function autoCheckDue(lastCheckedAt: number | null, now: number): boolean {
  return lastCheckedAt === null || !Number.isFinite(lastCheckedAt) || now - lastCheckedAt >= AUTO_CHECK_INTERVAL_MS
    || lastCheckedAt > now;   // 系统时间被往回调过：别因此永远不查
}

function readLastChecked(): number | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEYS.updateCheckedAt);
    return raw === null ? null : Number(raw);
  } catch {
    return null;
  }
}

function writeLastChecked(now: number) {
  try { window.localStorage.setItem(STORAGE_KEYS.updateCheckedAt, String(now)); } catch { /* 隐私模式等：下次再查 */ }
}

/** 下载、验签、安装新版并重启。用户在原生对话框里点「稍后」或安装失败时抛错（带原因），App 不会重启。 */
export async function installUpdate(): Promise<string> {
  const version = await invoke<string>("install_update");
  await invoke("restart_after_update");
  return version;
}

export function checkForUpdate(): Promise<UpdateCheck> {
  return invoke<UpdateCheck>("check_for_update");
}

/** 启动时调用：开着自动检查且到期才去问；任何失败都安静地当作没有新版。 */
export async function autoCheckForUpdate(now = Date.now()): Promise<UpdateCheck | null> {
  try {
    if (!await invoke<boolean>("get_update_check_enabled")) return null;
    if (!autoCheckDue(readLastChecked(), now)) return null;
    const result = await checkForUpdate();
    writeLastChecked(now);
    return result.newer ? result : null;
  } catch {
    return null;
  }
}
