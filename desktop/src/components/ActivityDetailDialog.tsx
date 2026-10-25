import { Check, Circle, CircleAlert, LoaderCircle, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { FeedStatus } from "../lib/activityFeed";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { formatActivityDuration } from "../lib/labels";
import "./ActivityDetailDialog.css";

export interface ActivityDetailStep {
  id: string; title: string; status: FeedStatus | "unknown" | "recorded"; detail?: string;
  recorded?: boolean; statusLabel?: string; recordedAt?: string; durationMs?: number;
}
const LABEL = { done: "记录完成", running: "执行中", waiting: "等你处理", failed: "失败", stopped: "已停止", unknown: "待核对", recorded: "已记录" };
const stepLabel = (step: ActivityDetailStep) => step.statusLabel
  || (step.recorded && step.status === "running" ? "记录为执行中" : LABEL[step.status]);
export function ActivityDetailDialog({ title, statusLabel = "记录详情", steps = [], children, onClose, actions }: {
  title: string; statusLabel?: string; steps?: ActivityDetailStep[]; children: ReactNode; onClose: () => void; actions?: ReactNode;
}) {
  const dialog = useDialogSurface();
  const heading = useId();
  const [selected, setSelected] = useState("result");
  const step = steps.find(item => item.id === selected);
  const recordedAt = typeof step?.recordedAt === "string" ? step.recordedAt : "";
  const recordedDate = recordedAt && Number.isFinite(Date.parse(recordedAt)) ? new Date(recordedAt) : null;
  const duration = typeof step?.durationMs === "number" && Number.isFinite(step.durationMs) && step.durationMs >= 0
    ? formatActivityDuration(step.durationMs) : "";
  const content = <dialog ref={dialog} className="activity-detail-dialog" aria-labelledby={heading}
    onKeyDown={event => { if (event.key === "Escape") event.stopPropagation(); }}
    onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <header><div><span>{statusLabel}</span><h2 id={heading}>{title}</h2></div>
      {actions}
      <button aria-label="关闭动态详情" onClick={onClose}><X size={18} /></button></header>
    <div className="activity-detail-layout">
      <nav aria-label="执行步骤"><p>过程记录</p><ol>{steps.map(item => <li key={item.id}>
        <button aria-current={step?.id === item.id ? "step" : undefined} onClick={() => setSelected(item.id)}>
          <StepIcon status={item.status} recorded={item.recorded} /><span><strong>{item.title}</strong><small>{stepLabel(item)}</small></span></button></li>)}</ol>
        <button className="activity-detail-result" aria-current={!step ? "step" : undefined} onClick={() => setSelected("result")}>
          <Check size={15} /><span>结果与后续</span></button>
      </nav>
      <section className="activity-detail-content" aria-label={step ? `步骤：${step.title}` : "结果与后续"} tabIndex={0}>
        {step ? <><small className="activity-detail-step-status">{stepLabel(step)}</small><h3>{step.title}</h3>
          {(recordedDate || duration) && <div className="activity-detail-metadata">
            {recordedDate && <span>记录时间 <time dateTime={recordedDate.toISOString()}>{recordedDate.toLocaleString([], { hour12: false })}</time></span>}
            {duration && <span>已记录耗时 {duration}</span>}
          </div>}
          <pre>{step.detail || "此步骤只保留了状态，暂无单独的结果记录。"}</pre></> : children}
      </section>
    </div>
    <footer>这里展示已记录的过程与结果；执行结束或收到回复，不代表已经验收通过。</footer>
  </dialog>;
  return typeof document === "undefined" ? content : createPortal(content,document.body);
}
function StepIcon({ status, recorded }: { status: ActivityDetailStep["status"]; recorded?: boolean }) {
  return status === "running" && !recorded ? <LoaderCircle size={15} className="session-step-spin" aria-hidden="true" />
    : status === "failed" ? <CircleAlert size={15} aria-hidden="true" />
    : status === "done" ? <Check size={15} aria-hidden="true" /> : <Circle size={15} aria-hidden="true" />;
}
