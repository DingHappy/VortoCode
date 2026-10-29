import type { CodexAction } from "../types";

/**
 * 动作的实际结果状态：failed 只算「确定没发出去」。旧版常驻电脑把「超时、回执对不上」这类说不准的也记成 failed，
 * 原因里写着「无法确认 / 可能已送达」——按原因认出来当 unknown，免得把可能已经发出的话说成没发。
 */
export function codexActionOutcome(action: Pick<CodexAction, "status" | "error">): CodexAction["status"] {
  if (action.status === "failed" && /无法确认|可能已送达/.test(action.error || "")) return "unknown";
  return action.status;
}
