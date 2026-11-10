// 错误报告与匿名使用统计的开关（原生层 src-tauri/src/telemetry.rs 存在本机）。错误报告默认开、使用统计默认关。
import { invoke } from "@tauri-apps/api/core";

export interface TelemetrySettings { errorsEnabled: boolean; usageEnabled: boolean }

export const PRIVACY_URL = "https://account.vortotech.com/privacy";

export function loadTelemetrySettings(): Promise<TelemetrySettings> {
  return invoke<TelemetrySettings>("telemetry_settings");
}

export function saveTelemetrySettings(next: TelemetrySettings): Promise<TelemetrySettings> {
  return invoke<TelemetrySettings>("set_telemetry_settings", { errorsEnabled: next.errorsEnabled, usageEnabled: next.usageEnabled });
}

/** 界面里没人接住的报错交给原生层：写诊断日志、按设置决定发不发错误报告。不在 Tauri 里（测试、预览）就算了。 */
export function reportUiError(kind: "ui.error" | "ui.unhandled_rejection", message: string): void {
  try {
    void invoke("report_ui_error", { kind, message: message.slice(0, 600) }).catch(() => undefined);
  } catch { /* 不在 Tauri 里 */ }
}
