import { useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { TaskItem } from "../types";
import { dispatchTaskIdentity } from "../lib/taskDispatch";

type Props = { task: TaskItem; getClient: () => GatewayClient | null; onUpdated: () => void };
type Action = "accept" | "rework" | "followup" | "cancel";

export function DispatchTaskActions(props: Props) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const current = useRef(props);
  current.current = props;
  const identity = dispatchTaskIdentity(props.task);
  if (!identity) return null;
  const { task } = props;
  const disconnected = !props.getClient();
  const canReview = task.dependencies?.result_valid !== false && task.status === "done" && task.collaboration?.review === "pending";
  const canFollowup = task.dependencies?.result_valid !== false && ["done", "failed", "cancelled", "interrupted"].includes(task.status)
    && task.collaboration?.review !== "accepted" && identity.round < 3
    && (!(task.dependencies?.requires?.length || task.dependencies?.failure) || task.dependencies?.resolution === "consumed");
  const canCancel = ["waiting", "queued", "running", "blocked"].includes(task.status);
  if (!canReview && !canFollowup && !canCancel) return null;

  const act = async (action: Action) => {
    const client = current.current.getClient();
    const snapshot = current.current.task;
    const owner = dispatchTaskIdentity(snapshot);
    if (!client || !owner || pending.current || (action !== "cancel" && !note.trim())) return;
    pending.current = true;
    setBusy(true);
    setError("");
    const isCurrent = () => current.current.getClient() === client && current.current.task.id === snapshot.id;
    try {
      if (action === "cancel") await client.cancelDelegation(snapshot.id, owner.session, owner.round);
      else if (action === "followup") await client.followupDelegation(snapshot.id, owner.session, owner.round, note.trim());
      else await client.reviewDelegation(snapshot.id, owner.session, owner.round, action, note.trim());
      if (isCurrent()) {
        setNote("");
        current.current.onUpdated();
      }
    } catch (reason) {
      if (isCurrent()) {
        setError(reason instanceof Error ? reason.message : "任务交互失败，请读取最新状态。");
        current.current.onUpdated();
      }
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };

  return <div className="dispatch-task-actions">
    {(canReview || canFollowup) && <label>处理说明<textarea value={note} maxLength={2000} disabled={busy}
      onChange={(event) => setNote(event.target.value)} placeholder="填写审查证据、缺项，或原任务范围内的补充要求" /></label>}
    <div className="task-actions">
      {canReview && <>
        <button disabled={busy || disconnected || !note.trim()} onClick={() => void act("accept")}>记录验收通过</button>
        <button disabled={busy || disconnected || !note.trim()} onClick={() => void act("rework")}>请求返工</button>
      </>}
      {canFollowup && <button disabled={busy || disconnected || !note.trim()} onClick={() => void act("followup")}>补充并执行下一轮</button>}
      {canCancel && <button disabled={busy || disconnected} onClick={() => void act("cancel")}>取消研究任务</button>}
    </div>
    {busy && <small>正在处理…</small>}
    {error && <div role="alert" className="task-error">{error}</div>}
  </div>;
}
