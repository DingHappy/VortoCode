import { useId, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { TaskItem } from "../types";
import { useTaskAction } from "../hooks/useTaskAction";
import { decideStaffAction, type StaffAction } from "../lib/staffAction";
import "./StaffActionCard.css";

type Props = { task: TaskItem; getClient: () => GatewayClient | null; onUpdated: () => void };
export function StaffActionCard({ task, getClient, onUpdated }: Props) {
  const card = task.collaboration?.approval;
  if (task.collaboration?.dispatch?.source !== "staff" || !card) return null;
  return <ActionForm key={`${task.id}:${card.payload.round}:${card.payload.id}`}
    card={card} waiting={task.status === "blocked"} getClient={getClient} onUpdated={onUpdated} />;
}

function ActionForm({ card, waiting, getClient, onUpdated }: Omit<Props, "task"> & { card: StaffAction; waiting: boolean }) {
  const client = getClient(), id = useId();
  const [reason, setReason] = useState("");
  const { busy, saved, error, run } = useTaskAction(client, getClient, onUpdated);
  const pending = waiting && card.status === "pending";
  const p = card.payload;
  const status = { pending: "专员等你批准这一步", approved: "这一步已批准", denied: "这一步已拒绝",
    cancelled: "这张审批卡已取消", expired: "这张审批卡已过期", interrupted: "审批等待已中断" }[card.status];
  async function decide(approve: boolean) {
    await run((client, isCurrent) => decideStaffAction(client, card, approve, reason, isCurrent));
  }
  return <section className="staff-action-card" aria-label="专员动作审批">
    <strong>{status}</strong>
    <p>专员 {p.member_name || p.member} · 岗位 {p.role_title || p.role} · 第 {p.round} 轮</p>
    <p className="staff-action-preview">{p.message}</p>
    <details><summary>核对完整动作内容</summary><pre>{JSON.stringify({ tool: p.tool, args: p.args }, null, 2)}</pre></details>
    {p.tainted && <p>本轮读过外部内容，请核对动作是否符合你的要求。</p>}
    {pending && <p>仅批准当前这一步，之后的动作仍须逐次确认。{p.required_key === "approval" ? "本次需要 Desktop 批准钥匙。" : "批准后会产生上述改动。"}</p>}
    {pending && <small>审批有效期至 {new Date(p.expires_at * 1000).toLocaleString()}。</small>}
    {card.reason && <p>你的理由：{card.reason}</p>}
    {["approved", "denied"].includes(card.status) && <p>审批决定不代表执行结果，请核对任务记录。</p>}
    {!pending && card.status !== "approved" && card.status !== "denied" && <p>旧卡不能继续执行。先核对任务历史，再决定是否重新派活。</p>}
    {pending && <>
      <label htmlFor={id}>拒绝理由（拒绝时必填）</label>
      <textarea id={id} maxLength={2000} value={reason} disabled={busy || saved}
        onChange={event => setReason(event.target.value)} />
      {error && <p className="task-error" role="alert">{error}</p>}
      {saved && <p role="status">决定已保存，正在刷新任务</p>}
      <div className="task-actions">
        <button type="button" onClick={onUpdated} disabled={busy}>刷新核对</button>
        <button type="button" disabled={!client || busy || saved || !reason.trim()} onClick={() => void decide(false)}>拒绝这一步</button>
        <button type="button" disabled={!client || busy || saved} onClick={() => void decide(true)}>{busy ? "处理中…" : "批准这一步"}</button>
      </div>
    </>}
  </section>;
}
