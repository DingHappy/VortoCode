import { useRef, useState } from "react";
import type { AssistantScheduleItem, AssistantScheduleRun } from "../types";

export function AssistantScheduleCard({ item, connected, onToggle, onHistory }: {
  item: AssistantScheduleItem; connected: boolean;
  onToggle: (item: AssistantScheduleItem) => Promise<void>;
  onHistory: (item: AssistantScheduleItem) => Promise<AssistantScheduleRun[]>;
}) {
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState<AssistantScheduleRun[] | null>(null);
  async function act(operation: () => Promise<void>) {
    if (pending.current || !connected) return;
    pending.current = true; setBusy(true); setError("");
    try { await operation(); }
    catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { pending.current = false; setBusy(false); }
  }
  const managed = item.kind === "cron" && Boolean(item.revision);
  return <li className={`assistant-schedule-card ${item.enabled ? "" : "disabled"}`}>
    <strong>{item.title}</strong>
    <small>{item.schedule} · {item.enabled ? "已启用" : "已暂停"}</small>
    {item.detail && <p>{item.detail}</p>}
    {item.last && <small>上次定时执行：{item.last.replace("T", " ")}</small>}
    {item.scheduler_enabled === false ? <small>此运行端的定时调度总开关未开启</small>
      : item.next && <small>下次：{item.next.replace("T", " ")}</small>}
    {Boolean(item.failures) && <small>连续失败 {item.failures} 次</small>}
    {managed ? <div className="assistant-task-actions">
      <button disabled={!connected || busy} onClick={() => void act(() => onToggle(item))}>{item.enabled ? "暂停" : "恢复"}</button>
      <button disabled={!connected || busy} onClick={() => void act(async () => setRuns(await onHistory(item)))}>执行记录</button>
    </div> : <small>由运行端配置管理</small>}
    {!connected && <small>连接断开，暂时无法核对或操作</small>}
    {error && <p role="alert">{error}</p>}
    {runs && <div className="assistant-schedule-history">
      <small>最近保留的执行记录（最多 10 条）</small>
      {runs.length === 0 && <p>没有找到保留的执行记录</p>}
      {runs.map(run => <article key={run.id}>
        <small>{run.created.replace("T", " ")} · {run.status === "done" ? "执行成功" : run.status === "failed" ? "执行失败" : run.status}</small>
        <p>{run.summary || "无结果摘要"}</p>{run.error && <p role="alert">{run.error}</p>}
      </article>)}
    </div>}
  </li>;
}
