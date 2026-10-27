// 本机诊断日志的前端入口：只传事件名和一句简短说明，写文件、脱敏、截断、轮转都在原生层
// （src-tauri/src/diagnostics.rs）。不传对话内容和任何密钥；写失败一律忽略，不影响界面。
import { invoke } from "@tauri-apps/api/core";

export type DiagLevel = "info" | "warn" | "error";

export function diag(event: string, detail = "", level: DiagLevel = "info"): void {
  try {
    void invoke("diag_log", { level, event, detail: detail.slice(0, 600) }).catch(() => undefined);
  } catch { /* 不在 Tauri 里（测试、预览）：不记 */ }
}

function reasonText(reason: unknown): string {
  return reason instanceof Error ? `${reason.name}: ${reason.message}` : String(reason);
}

/** 界面里没人接住的报错也记下来：以前这类错误只在开发者工具里出现，装好的 App 里谁也看不到。 */
export function installGlobalErrorLogging(target: Pick<Window, "addEventListener"> = window): void {
  target.addEventListener("error", (event) => {
    const error = event as ErrorEvent;
    diag("ui.error", `${error.message} @ ${error.filename}:${error.lineno}`, "error");
  });
  target.addEventListener("unhandledrejection", (event) => {
    diag("ui.unhandled_rejection", reasonText((event as PromiseRejectionEvent).reason), "error");
  });
}
