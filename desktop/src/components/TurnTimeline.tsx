// App.tsx 抽出的 Agent 执行时间线组件（B8-④b S1，只搬家不改行为）。
// 把一回合内的 phase / tool / hook 活动折叠成一条可展开的时间线。
// activityIcon / activityDetails 是它的私有辅助函数，随组件一起搬出。
import {
  BrainCircuit,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  FileSearch,
  LoaderCircle,
  Search,
  SquareTerminal,
  Wrench,
} from "lucide-react";
import type { ReactNode } from "react";

import { formatActivityDuration } from "../lib/labels";
import type { TurnActivity } from "../types";

function activityIcon(activity: TurnActivity): ReactNode {
  if (["failed", "blocked", "cancelled", "timed_out"].includes(activity.status)) return <CircleAlert size={15} />;
  if (activity.status === "running") return <LoaderCircle className="activity-spinner" size={15} />;
  if (activity.kind === "phase") return activity.phase === "thinking" ? <BrainCircuit size={15} /> : <CircleCheck size={15} />;
  if (activity.name === "run_command") return <SquareTerminal size={15} />;
  if (activity.name === "read_file" || activity.name === "list_files") return <FileSearch size={15} />;
  if (activity.name?.includes("search") || activity.name === "grep") return <Search size={15} />;
  return <Wrench size={15} />;
}

function activityDetails(activity: TurnActivity): string {
  const sections: string[] = [];
  if (activity.kind === "hook") {
    sections.push(`事件：${activity.event ?? "unknown"}${activity.tool ? `\n工具：${activity.tool}` : ""}`);
  }
  if (activity.args && Object.keys(activity.args).length > 0) sections.push(JSON.stringify(activity.args, null, 2));
  if (activity.result) sections.push(activity.result);
  if (activity.error) sections.push(`错误：${activity.error}`);
  return sections.join("\n\n");
}

export function TurnTimeline({ items, active }: { items: TurnActivity[]; active: boolean }) {
  if (items.length === 0) return null;
  const phaseDuration = items
    .filter((item) => item.kind === "phase")
    .reduce((total, item) => total + (item.durationMs ?? 0), 0);
  const tools = items.filter((item) => item.kind === "tool");
  const hooks = items.filter((item) => item.kind === "hook");
  return (
    <section className={`turn-timeline ${active ? "active" : ""}`} aria-label="Agent 执行过程" aria-live={active ? "polite" : "off"}>
      <div className="timeline-heading">
        <span>{active ? "正在工作" : phaseDuration > 0 ? `已工作 ${formatActivityDuration(phaseDuration)}` : "执行过程"}</span>
        {(tools.length > 0 || hooks.length > 0) && <small>{tools.length > 0 ? `${tools.length} 个工具` : ""}{tools.length > 0 && hooks.length > 0 ? " · " : ""}{hooks.length > 0 ? `${hooks.length} 个 Hook` : ""}</small>}
      </div>
      <div className="timeline-list">
        {items.map((activity) => {
          const details = activityDetails(activity);
          const row = (
            <>
              <span className={`timeline-icon ${activity.status}`}>{activityIcon(activity)}</span>
              <span className="timeline-label">{activity.label}</span>
              {activity.durationMs != null && <time>{formatActivityDuration(activity.durationMs)}</time>}
            </>
          );
          return (activity.kind === "tool" || activity.kind === "hook") && details ? (
            <details className={`timeline-row ${activity.kind} ${activity.status}`} key={activity.id}>
              <summary>{row}<ChevronDown className="timeline-chevron" size={14} /></summary>
              <pre>{details}</pre>
            </details>
          ) : (
            <div className={`timeline-row ${activity.kind} ${activity.status}`} key={activity.id}>{row}</div>
          );
        })}
      </div>
    </section>
  );
}
