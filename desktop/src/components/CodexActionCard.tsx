// 对话里的「助理想替你给 Codex 发…」卡片：拟发的原文摆在卡片上，点「批准发送」就交给 Mac 上的 Codex，不再弹窗。
import { useState } from "react";

import type { CodexAction } from "../types";

const STATUS: Record<CodexAction["status"], string> = {
  proposed: "等你批准", approved: "已批准，尚未确认交给 Codex", sending: "动作已被认领，发送结果待核对",
  sent: "请求已获 Codex 受理", failed: "动作未能确认送达", cancelled: "已撤回", expired: "已过时，没发",
};

export function CodexActionCard({ action, connected, onDecide }: {
  action: CodexAction;
  connected: boolean;
  onDecide: (decision: "approve" | "cancel") => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const decide = async (decision: "approve" | "cancel") => {
    setBusy(true); setError("");
    try { await onDecide(decision); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败，请稍后再试"); }
    finally { setBusy(false); }
  };
  const interrupt = action.kind === "interrupt";
  return <article className="assistant-task-card codex-action-card" aria-label={`Codex 动作：${action.thread_name}`}>
    <header><strong>{interrupt ? "建议叫停 Codex" : "替你给 Codex 发话"}</strong><span>「{action.thread_name}」</span></header>
    <p className="assistant-task-status">{STATUS[action.status] ?? action.status}</p>
    {action.status === "proposed" && <div className="task-actions">
      <button className="primary" disabled={busy || !connected} onClick={() => void decide("approve")}>{interrupt ? "批准叫停" : "批准发送"}</button>
      <button disabled={busy || !connected} onClick={() => void decide("cancel")}>不发</button>
    </div>}
    {interrupt
      ? <p className="assistant-task-text">叫停它正在做的这一轮（已经做的改动会保留）。</p>
      : <p className="assistant-task-text">{action.text}</p>}
    {action.tainted && <p className="assistant-task-warning">助理是在读过外部内容后拟的，发之前看清楚。</p>}
    {(action.status === "failed" || action.status === "expired") && action.error && <p className="task-error">{action.error}</p>}
    {!connected && <small>连接中断，恢复后可继续处理。</small>}
    {error && <p role="alert" className="task-error">{error}</p>}
  </article>;
}
