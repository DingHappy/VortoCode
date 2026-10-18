// 检查更新：问 GitHub 最新正式版（Rust 侧 check_for_update），有新版就给一个打开下载页的入口。
// 不下载、不安装；自动检查每天最多一次，可在设置里关掉（关了就完全不联系 GitHub）。
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
