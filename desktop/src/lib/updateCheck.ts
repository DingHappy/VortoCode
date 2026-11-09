// 检查更新：问 GitHub 最新正式版（Rust 侧 check_for_update），有新版就提示。
// 自动检查每天最多一次，只比版本号，可在设置里关掉（关了就完全不联系 GitHub）。
// 安装只在用户点了「更新并重启」后发生：Rust 侧弹原生确认，下载后用内置公钥验签，通过才替换并重启。
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

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

/** Rust 侧下载时发的 update-progress 事件；attempt 从 1 起，网络失败自动重试时加一。 */
export interface UpdateProgress {
  downloaded: number;
  total: number | null;
  attempt: number;
}

/** 按钮上的进度文字：知道总大小显示百分比，不知道显示已下载 MB；重试时注明第几次；下完就是在安装。 */
export function progressText(progress: UpdateProgress | null): string {
  if (!progress) return "正在更新…";
  const retry = progress.attempt > 1 ? `（第 ${progress.attempt} 次）` : "";
  const { downloaded, total } = progress;
  if (total && total > 0) {
    if (downloaded >= total) return "正在安装…";
    return `正在下载 ${Math.floor(downloaded * 100 / total)}%${retry}`;
  }
  return `正在下载 ${(downloaded / 1048576).toFixed(1)} MB${retry}`;
}

/** 下载、验签、安装新版，装好后由 Rust 侧直接重启。用户在原生对话框里点「稍后」或安装失败时抛错（带原因），App 不会重启。
 * 网络失败 Rust 侧会自动重试；每次失败的原因写进诊断日志（设置 → 高级 → 诊断日志）。 */
export async function installUpdate(onProgress?: (progress: UpdateProgress) => void): Promise<string> {
  let unlisten: (() => void) | undefined;
  if (onProgress) {
    try { unlisten = await listen<UpdateProgress>("update-progress", (event) => onProgress(event.payload)); }
    catch { /* 拿不到进度也照常更新 */ }
  }
  try {
    // 装好后 Rust 侧直接重启，不再由网页请求：替换 App 时网页进程可能已经没了
    return await invoke<string>("install_update");
  } finally {
    unlisten?.();
  }
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
