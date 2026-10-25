// App.tsx 抽出的 Agent 执行时间线组件（B8-④b S1，只搬家不改行为）。
// 把一回合内的 phase / tool / hook 活动折叠成一条可展开的时间线。
// activityIcon / activityDetails 是它的私有辅助函数，随组件一起搬出。
import {
  BrainCircuit,
  ChevronDown,
  Circle,
  CircleAlert,
  CircleCheck,
  Cpu,
  FileSearch,
  LoaderCircle,
  Search,
  SquareTerminal,
  Wrench,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { formatActivityDuration } from "../lib/labels";
import { recapPhrases, turnRecap } from "../lib/turnRecap";
import type { TurnActivity } from "../types";

const unknownStatus = (status: string) => !["running", "completed", "succeeded", "failed", "blocked", "cancelled", "timed_out"].includes(status);

function activityIcon(activity: TurnActivity, active: boolean): ReactNode {
  if (["failed", "blocked", "cancelled", "timed_out"].includes(activity.status)) return <CircleAlert size={15} />;
  if (activity.status === "running") return active ? <LoaderCircle className="activity-spinner" size={15} /> : <Circle size={15} />;
  if (unknownStatus(activity.status)) return <Circle size={15} />;
  if (activity.kind === "phase" && activity.phase === "routing") return <Cpu size={15} />;
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

export function timelineExpanded(items: TurnActivity[], active: boolean, hasOutput: boolean): boolean {
  return active || !hasOutput || items.some(item => unknownStatus(item.status) || ["failed", "blocked", "cancelled", "timed_out", "running"].includes(item.status));
}

export function TurnTimeline({ items, active, hasOutput = false }: { items: TurnActivity[]; active: boolean; hasOutput?: boolean }) {
  const automaticOpen = timelineExpanded(items, active, hasOutput);
  const [open, setOpen] = useState(automaticOpen);
  useEffect(() => { setOpen(automaticOpen); }, [automaticOpen]);
  if (items.length === 0) return null;
  const phaseDuration = items
    .filter((item) => item.kind === "phase")
    .reduce((total, item) => total + (item.durationMs ?? 0), 0);
  const tools = items.filter((item) => item.kind === "tool");
  const hooks = items.filter((item) => item.kind === "hook");
  const attention = items.filter(item => unknownStatus(item.status) || (!active && item.status === "running")
    || ["failed", "blocked", "cancelled", "timed_out"].includes(item.status));
  return (
    <section className={`turn-timeline ${active ? "active" : ""}`} aria-label="Agent 执行过程" aria-live={active ? "polite" : "off"}>
      <button type="button" className="timeline-heading" aria-expanded={open} onClick={() => setOpen(value => !value)}>
        <ChevronDown size={14} className={open ? "" : "timeline-collapsed"} />
        <span>{active ? "正在工作" : "执行过程"}{phaseDuration > 0 ? ` · ${formatActivityDuration(phaseDuration)}` : ""}</span>
        {attention.length > 0 && <strong className="timeline-attention">{attention.length} 项需要关注</strong>}
        {(tools.length > 0 || hooks.length > 0) && <small>{[
          // 结束后折叠的标题就是这一轮的回顾（改了几个文件、测试过没过…），不用展开也知道做了什么
          ...(active ? (tools.length > 0 ? [`${tools.length} 个工具`] : []) : recapPhrases(turnRecap(items))),
          ...(hooks.length > 0 ? [`${hooks.length} 个 Hook`] : []),
        ].join(" · ")}</small>}
      </button>
      {open && <div className="timeline-list">
        {items.map((activity) => {
          const details = activityDetails(activity);
          const row = (
            <>
              <span className={`timeline-icon ${activity.status}`}>{activityIcon(activity, active)}</span>
              <span className="timeline-label">{activity.label}
                {unknownStatus(activity.status) ? <small> · 状态待核对</small>
                  : !active && activity.status === "running" ? <small> · 记录为执行中</small> : null}</span>
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
      </div>}
    </section>
  );
}
