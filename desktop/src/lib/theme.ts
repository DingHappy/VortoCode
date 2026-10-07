// 外观主题：跟随系统 / 浅色 / 深色。
//
// 深色配色在 App.css 里由 prefers-color-scheme 驱动；用户手动选浅色或深色时，在 <html> 上写
// data-theme 覆盖系统。偏好只存本机 localStorage——读写失败（隐私模式、被清理）时一律退回
// 跟随系统，不影响渲染。
import { STORAGE_KEYS } from "./storage";

export type ThemePreference = "system" | "light" | "dark";

export const THEME_OPTIONS: Array<{ value: ThemePreference; label: string }> = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

type KeyValueStorage = Pick<Storage, "getItem" | "setItem">;

function defaultStorage(): KeyValueStorage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export function readThemePreference(storage: KeyValueStorage | null = defaultStorage()): ThemePreference {
  try {
    const value = storage?.getItem(STORAGE_KEYS.theme);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function saveThemePreference(
  preference: ThemePreference,
  storage: KeyValueStorage | null = defaultStorage(),
): void {
  try {
    storage?.setItem(STORAGE_KEYS.theme, preference);
  } catch {
    // 存不下就只在本次运行生效。
  }
}

export function applyThemePreference(
  preference: ThemePreference,
  root: Pick<HTMLElement, "setAttribute" | "removeAttribute"> | null =
    typeof document === "undefined" ? null : document.documentElement,
): void {
  if (!root) return;
  if (preference === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", preference);
}
