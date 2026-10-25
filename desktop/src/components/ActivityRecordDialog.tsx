import type { GatewayClient } from "../gateway";
import type { AgentTurnDetail, TurnActivity, AssistantDispatch } from "../types";
import type { FeedItem } from "../lib/activityFeed";
import { hydrateActivities } from "../protocol/activities";
import { ActivityDetailDialog, type ActivityDetailStep } from "./ActivityDetailDialog";
import { MarkdownMessage } from "./MarkdownMessage";
import { useActivityRecord } from "../hooks/useActivityRecord";
import { RefreshCw } from "lucide-react";

export function activityRecordSteps(detail: AgentTurnDetail | null): ActivityDetailStep[] {
  if (!detail) return [];
  const state = (value: string): ActivityDetailStep["status"] => ["completed","succeeded"].includes(value) ? "done"
    : value === "running" ? "running" : ["failed","blocked","timed_out"].includes(value) ? "failed"
    : value === "cancelled" ? "stopped" : "unknown";
  const text = (item: TurnActivity) => [item.status === "running"
    ? "这是保存时的执行中状态；当前是否仍在执行尚未核实。"
    : state(item.status) === "unknown" ? "未取得可核对的步骤状态，不能据此判断仍在执行或已经完成。" : "",
    item.kind === "hook" ? [item.event ? `事件：${item.event}` : "", item.tool ? `工具：${item.tool}` : ""].filter(Boolean).join("\n") : "",
    item.args ? `参数\n${JSON.stringify(item.args,null,2)}` : "",item.result || "",item.error || ""].filter(Boolean).join("\n\n");
  const latest = new Map(detail.events.map(event => [event.id, event]));
  return [{ id: "request", title: "你的请求", status: "recorded", detail: detail.request },
    ...hydrateActivities(detail.events, { missingStatus: "unknown" }).map(item => ({ id: `event:${item.id}`,
      title: item.kind === "phase" && item.label === "正在工作" ? "阶段记录" : item.label,
      status: state(item.status), detail: text(item), recorded: true, durationMs: item.durationMs,
      recordedAt: latest.get(item.id)?.recorded_at,
      statusLabel: item.status === "timed_out" ? "超时" : item.status === "blocked" ? "受阻" : undefined }))];
}

export function dispatchDetailSteps(task: AssistantDispatch): ActivityDetailStep[] {
  return (task.progress?.steps ?? []).map((step,index) => ({ id: `progress:${index}`, title: step.title || `第${index+1}步`,
    status: step.status === "landed" ? "done" : step.status === "running" ? "running" : step.status === "failed" ? "failed" : "unknown" }));
}

export function ActivityRecordDialog({ item, client, onClose, onNavigate }: {
  item: FeedItem; client?: Pick<GatewayClient,"getAgentActivityDetail"> | null; onClose: () => void; onNavigate: () => void;
}) {
  const { record, loading, error, checkedAt, readable, refresh } = useActivityRecord(client, item.session, item.rid);
  const statusLabel = error ? record ? "上次记录 · 待核对" : "摘要记录"
    : record ? record.has_reply ? "已记录回复" : "暂无最终回复" : item.statusLabel || "动态记录";
  return <ActivityDetailDialog title={item.title} statusLabel={statusLabel}
    steps={activityRecordSteps(record)} onClose={onClose}
    actions={readable && <button className="activity-detail-refresh" disabled={loading} onClick={refresh}
      aria-label={error ? "重新读取记录" : "刷新过程"} title={error ? "重新读取记录" : "刷新过程"}>
      <RefreshCw size={14} className={loading ? "session-step-spin" : undefined} />{loading ? "读取中" : error ? "重新读取" : "刷新过程"}</button>}>
    <h3>结果与后续</h3>
    {loading && <p role="status">{record ? "正在刷新过程，先保留上次记录…" : "正在读取已保存的过程…"}</p>}
    {error && <p className="activity-record-hint" role="status">{error}</p>}
    {checkedAt !== null && <p className="activity-record-hint">最近读取 {new Date(checkedAt).toLocaleTimeString([], { hour12: false })}
      {record && !record.has_reply && !error ? " · 打开期间自动核对记录" : ""}</p>}
    {!record && !loading && !error && <p className="activity-record-hint">{item.rid ? "当前仅有这条动态的摘要，尚未取得逐步记录。" : "这条记录没有单独的步骤明细。"}</p>}
    {record?.reply ? <MarkdownMessage text={record.reply} /> : <p style={{whiteSpace:"pre-wrap"}}>{item.detail || item.summary || "暂无结果记录。"}</p>}
    {record && !record.has_reply && <p className="activity-record-hint">没有找到最终回复，不能据此判断已经完成。</p>}
    {record?.truncated && <p className="activity-record-hint">部分长内容已截断，敏感字段已隐藏；完整上下文请查看原对话。</p>}
    <div className="activity-record-actions">
      {(item.session || item.taskId) && <button onClick={() => { onClose(); onNavigate(); }}>{item.taskId ? "打开项目任务" : "打开原对话"}</button>}
    </div>
  </ActivityDetailDialog>;
}
