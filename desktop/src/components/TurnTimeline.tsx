// 一回合内的执行记录：摘要保留异常，原始参数和结果按需展开。
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
import { useState, type ReactNode } from "react";

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

function activityDetails(activity: TurnActivity): { title: string; text: string }[] {
  const sections: { title: string; text: string }[] = [];
  if (activity.name) sections.push({ title: activity.kind === "hook" ? "Hook" : "工具", text: activity.name });
  if (activity.kind === "hook") {
    sections.push({ title: "触发事件", text: `${activity.event ?? "unknown"}${activity.tool ? `\n工具：${activity.tool}` : ""}` });
  }
  if (activity.args && Object.keys(activity.args).length > 0) sections.push({ title: "调用参数", text: JSON.stringify(activity.args, null, 2) });
  if (activity.result != null) sections.push({ title: "返回结果", text: activity.result || "（返回内容为空）" });
  if (activity.error) sections.push({ title: "错误信息", text: activity.error });
  return sections;
}

function activityStatus(activity: TurnActivity, active: boolean): string {
  if (unknownStatus(activity.status)) return "状态待核对";
  if (activity.status === "running") return active ? "执行中" : "记录为执行中";
  return ({ completed: "已完成", succeeded: "已完成", failed: "失败", blocked: "调用受阻", cancelled: "已取消", timed_out: "已超时" })[activity.status] ?? "状态待核对";
}

export function timelineExpanded(items: TurnActivity[], active: boolean, hasOutput: boolean): boolean {
  return active || !hasOutput || items.some(item => unknownStatus(item.status) || ["failed", "blocked", "cancelled", "timed_out", "running"].includes(item.status));
}

export function TurnTimeline({ items, active, hasOutput = false }: { items: TurnActivity[]; active: boolean; hasOutput?: boolean }) {
  const automaticOpen = timelineExpanded(items, active, hasOutput);
  const [openOverride, setOpenOverride] = useState<boolean | null>(null);
  const open = openOverride ?? automaticOpen;
  if (items.length === 0) return null;
  const phaseDuration = items
    .filter((item) => item.kind === "phase")
    .reduce((total, item) => total + (item.durationMs ?? 0), 0);
  const tools = items.filter((item) => item.kind === "tool");
  const hooks = items.filter((item) => item.kind === "hook");
  const singleTool = !active && tools.length === 1 && hooks.length === 0 ? tools[0] : null;
  const summaryDuration = singleTool ? singleTool.durationMs ?? 0 : phaseDuration;
  const recap = turnRecap(items);
  const specificRecap = recap.filesChanged.length > 0 || recap.tests.runs > 0 || recap.commands > 0 || recap.devRuns > 0;
  const attention = items.filter(item => unknownStatus(item.status) || (!active && item.status === "running")
    || ["failed", "blocked", "cancelled", "timed_out"].includes(item.status));
  return (
    <section className={`turn-timeline ${active ? "active" : ""}`} aria-label="Agent 执行过程" aria-live={active ? "polite" : "off"}>
      <button type="button" className="timeline-heading" aria-expanded={open} onClick={() => setOpenOverride(!open)}>
        <ChevronDown size={14} className={open ? "" : "timeline-collapsed"} />
        <span>{singleTool ? singleTool.label || singleTool.name || "工具调用" : active ? "正在工作" : "执行过程"}{summaryDuration > 0 ? ` · ${formatActivityDuration(summaryDuration)}` : ""}</span>
        {attention.length > 0 && <strong className="timeline-attention">{attention.length} 项需要关注</strong>}
        {(tools.length > 0 || hooks.length > 0) && <small>{[
          // 结束后折叠的标题就是这一轮的回顾（改了几个文件、测试过没过…），不用展开也知道做了什么
          ...(singleTool ? [activityStatus(singleTool, active), ...(!active && specificRecap ? recapPhrases(recap) : [])]
            : active ? (tools.length > 0 ? [`${tools.length} 个工具`] : []) : recapPhrases(recap)),
          ...(hooks.length > 0 ? [`${hooks.length} 个 Hook`] : []),
        ].join(" · ")}</small>}
      </button>
      {open && <div className="timeline-list">
        {items.map((activity) => {
          const details = activityDetails(activity);
          const row = (
            <>
              <span className={`timeline-icon ${activity.status}`}>{activityIcon(activity, active)}</span>
              <span className="timeline-label">{activity.label}</span>
              <span className="timeline-meta"><span className={`timeline-status ${activity.status}`}>{activityStatus(activity, active)}</span>
                {activity.durationMs != null && <time>{formatActivityDuration(activity.durationMs)}</time>}
              </span>
            </>
          );
          return (activity.kind === "tool" || activity.kind === "hook") && details.length > 0 ? (
            <details className={`timeline-row ${activity.kind} ${activity.status}`} key={activity.id}>
              <summary>{row}<ChevronDown className="timeline-chevron" size={14} /></summary>
              <div className="timeline-details">
                {details.map(section => <div className="timeline-detail" key={section.title}>
                  <h4>{section.title}</h4><pre>{section.text}</pre>
                </div>)}
              </div>
            </details>
          ) : (
            <div className={`timeline-row ${activity.kind} ${activity.status}`} key={activity.id}>{row}</div>
          );
        })}
      </div>}
    </section>
  );
}
