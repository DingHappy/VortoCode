import type { CodexAction } from "../types";

/**
 * 动作的实际结果状态：failed 只算「确定没发出去」。旧版常驻电脑把「超时、回执对不上」这类说不准的也记成 failed，
 * 原因里写着「无法确认 / 可能已送达」——按原因认出来当 unknown，免得把可能已经发出的话说成没发。
 */
export function codexActionOutcome(action: Pick<CodexAction, "status" | "error">): CodexAction["status"] {
  if (action.status === "failed" && /无法确认|可能已送达/.test(action.error || "")) return "unknown";
  return action.status;
}

const RECENT_OUTCOME_MS = 10 * 60_000;

/**
 * 对话末尾显示哪些 Codex 动作卡片：还要你处理的（等批准、发送待核对、结果无法确认），加上刚有结果的（10 分钟内）。
 * 更早办完的只在「动态」里——真机 2026-10-29：已受理、没发出去的旧卡片一直堆在对话最下面，把等批的那张淹没了。
 * 在别的对话里拟的不显示；监管拟的（不属于哪段对话）只在等批准时显示。
 */
export function chatCodexActions(actions: CodexAction[], activeSid: string, now = Date.now()): CodexAction[] {
  return actions.filter(action => {
    if (action.status === "cancelled") return false;
    if (!(action.session ? action.session.replace(/^sid-/, "") === activeSid : action.status === "proposed")) return false;
    const outcome = codexActionOutcome(action);
    if (outcome === "proposed" || outcome === "approved" || outcome === "sending" || outcome === "unknown") return true;
    const at = Date.parse(action.updated_at || action.created_at);
    return Number.isFinite(at) && now - at < RECENT_OUTCOME_MS;
  }).slice(0, 6);
}
