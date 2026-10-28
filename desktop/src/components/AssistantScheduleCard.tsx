import { Clock3, Eye, HeartPulse, Newspaper, Target } from "lucide-react";
import { useRef, useState } from "react";
import { scheduleLabel, whenLabel } from "../lib/time";
import type { AssistantScheduleItem, AssistantScheduleRun } from "../types";

// 每种定时一个图标，和面板里其它列表行一样放在左侧
const KIND_ICON: Record<AssistantScheduleItem["kind"], typeof Clock3> = {
  heartbeat: HeartPulse, report: Newspaper, watch: Eye, goal: Target, cron: Clock3,
};

export function AssistantScheduleCard({ item, connected, now, onToggle, onHistory }: {
  item: AssistantScheduleItem; connected: boolean; now?: Date;
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
  const unscheduled = item.scheduler_enabled === false;
  const times = [item.last && `上次 ${whenLabel(item.last, now)}`, !unscheduled && item.next && `下次 ${whenLabel(item.next, now)}`].filter(Boolean);
  const Icon = KIND_ICON[item.kind] ?? Clock3;
  return <li className={`assistant-schedule-card ${item.kind}${item.enabled ? "" : " disabled"}`}>
    <span className="assistant-schedule-icon" aria-hidden="true"><Icon /></span>
    <div className="assistant-schedule-main">
    <header>
      <strong>{item.title}</strong>
      {!managed && item.kind !== "goal" && <span className="assistant-schedule-tag" title="由运行端配置管理">只读</span>}
      <span className={`assistant-schedule-state${item.enabled ? " on" : ""}`}>{item.enabled ? "已启用" : "已暂停"}</span>
    </header>
    <small className="assistant-schedule-when">{scheduleLabel(item.schedule, now)}</small>
    {item.detail && <p>{item.detail}</p>}
    {times.length > 0 && <small>{times.join(" · ")}</small>}
    {unscheduled && <small className="assistant-schedule-warn">{item.kind === "goal" ? "这台电脑上没有通知渠道，目标提醒发不出去（需要常驻电脑）" : "这台电脑上调度没开，不会按时自动跑（只用 Desktop 时就是这样；要按时跑需要常驻电脑）"}</small>}
    {Boolean(item.failures) && <small className="assistant-schedule-warn">连续失败 {item.failures} 次</small>}
    {item.last_status === "timed_out" && <small className="assistant-schedule-warn">上次巡检超时</small>}
    {item.last_status === "cancelled" && <small className="assistant-schedule-warn">上次巡检被中断</small>}
    {item.last_status === "running" && <small>上次巡检尚无完成记录，可能仍在执行或已中断</small>}
    {managed ? <div className="assistant-task-actions">
      <button disabled={!connected || busy} onClick={() => void act(() => onToggle(item))}>{item.enabled ? "暂停" : "恢复"}</button>
      <button disabled={!connected || busy} onClick={() => void act(async () => setRuns(await onHistory(item)))}>执行记录</button>
    </div> : item.kind === "goal" && <small>在个人目标卡中核对或暂停</small>}
    {!connected && <small>连接断开，暂时无法核对或操作</small>}
    {error && <p role="alert">{error}</p>}
    {runs && <div className="assistant-schedule-history">
      <small>最近保留的执行记录（最多 10 条）</small>
      {runs.length === 0 && <p>没有找到保留的执行记录</p>}
      {runs.map(run => <article key={run.id}>
        <small>{whenLabel(run.created, now)} · {run.status === "done" ? "执行成功" : run.status === "failed" ? "执行失败" : run.status}</small>
        <p>{run.summary || "无结果摘要"}</p>{run.error && <p role="alert">{run.error}</p>}
      </article>)}
    </div>}
    </div>
  </li>;
}
