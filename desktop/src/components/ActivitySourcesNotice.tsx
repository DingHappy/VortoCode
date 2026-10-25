import { RefreshCw } from "lucide-react";
import { activityCoverage, ACTIVITY_SOURCE_LABEL, type ActivitySource } from "../lib/activitySources";

export function ActivitySourcesNotice({ sources, connected, now, onRefresh }: {
  sources: ActivitySource[]; connected: boolean; now: number; onRefresh?: () => void;
}) {
  const coverage = activityCoverage(sources, connected, now);
  const checking = sources.some(source => source.checking || source.status === "loading");
  return <div className={`activity-sources ${coverage.complete ? "current" : "uncertain"}`}>
    <div className="activity-sources-heading">
      <span>{coverage.complete ? `最近完整核对 ${new Date(coverage.checkedAt!).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`
        : connected ? "动态来源待核对，数量可能不完整" : "历史记录，当前进展待核对"}</span>
      {onRefresh && <button aria-label="刷新动态" title="只读核对各来源" disabled={!connected} onClick={onRefresh}><RefreshCw size={14} aria-hidden="true" className={connected && checking ? "session-step-spin" : ""} /></button>}
    </div>
    {!coverage.complete && <details><summary>查看来源状态</summary>
      {sources.length ? <ul>{sources.map(source => <li key={source.name}>
        {ACTIVITY_SOURCE_LABEL[source.name]}：{!connected ? "历史记录" : source.checking || source.status === "loading" ? "正在核对"
          : source.status === "unavailable" ? "未能取得最新记录" : coverage.issues.includes(source) ? "上次读取已过期" : "已核对"}
        {source.checkedAt !== undefined && <small> · 上次成功 {new Date(source.checkedAt).toLocaleString("zh-CN")}</small>}
      </li>)}</ul> : <p>尚未取得来源状态，不能确认是否还有其他待处理事项。</p>}
    </details>}
  </div>;
}
