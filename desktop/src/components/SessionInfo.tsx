import { ChevronRight, Circle, CircleAlert, CircleCheck, LoaderCircle, RefreshCw } from "lucide-react";
import type { PlanItem, SessionContextUsage, TurnActivity } from "../types";
import { formatActivityDuration } from "../lib/labels";
import { ContextStatus } from "./ContextStatus";
export { ContextStatus } from "./ContextStatus";

const activityState = (status: string) => ({ running: "进行中", completed: "已结束", succeeded: "已完成", failed: "失败", blocked: "等待处理", cancelled: "已取消", timed_out: "超时" } as Record<string, string>)[status] ?? "状态未知";
const stepState = (status: string) => status === "completed" ? "已完成" : status === "in_progress" ? "进行中" : status === "pending" ? "待执行" : activityState(status);
/** 步骤的视觉档：完成 / 进行中 / 待执行 / 出错（失败、超时、未知状态）/ 等待（阻塞、取消）。 */
const stepTone = (status: string) => status === "completed" ? "done" : status === "in_progress" ? "active" : status === "pending" ? "pending"
  : status === "blocked" || status === "cancelled" ? "warn" : "alert";
/** 执行记录的视觉档，与步骤共用一套颜色：运行中 / 完成 / 出错 / 等待。 */
const activityTone = (status: string) => status === "running" ? "active" : status === "completed" || status === "succeeded" ? "done"
  : status === "blocked" || status === "cancelled" ? "warn" : status === "failed" || status === "timed_out" ? "alert" : "done";
function StepIcon({ tone }: { tone: string }) {
  if (tone === "done") return <CircleCheck size={16} aria-hidden="true" />;
  if (tone === "active") return <LoaderCircle size={16} aria-hidden="true" className="session-step-spin" />;
  if (tone === "alert" || tone === "warn") return <CircleAlert size={16} aria-hidden="true" />;
  return <Circle size={16} aria-hidden="true" />;
}

export function SessionInfo({ context, busy, connected, needsInput = false, plan, activities, queued, model, onRefresh, onOpenSettings }: {
  context?: SessionContextUsage; busy: boolean; connected: boolean; needsInput?: boolean; plan: PlanItem[];
  activities: TurnActivity[]; queued: number; model: string; onRefresh: () => void; onOpenSettings?: () => void;
}) {
  const hasWork = plan.length > 0 || activities.length > 0;
  const attention = activities.filter(item => ["failed", "blocked", "cancelled", "timed_out"].includes(item.status));
  const done = plan.filter(item => item.status === "completed").length;
  // 没有计划时（大多数日常对话），侧栏改列本回合真正做了什么：每次工具调用/阶段一行，带耗时。
  const turnSteps = plan.length === 0 ? activities.filter(item => item.kind !== "event") : [];
  const turnDuration = turnSteps.filter(item => item.kind === "phase").reduce((total, item) => total + (item.durationMs ?? 0), 0);
  const turnTools = turnSteps.filter(item => item.kind === "tool").length;
  // 侧栏只放步骤。常态（就绪/运行中）不单独占一块——运行中由步骤高亮与标签上的小点表达；
  // 只有要人动手的状态（断线、等确认）才出一条提示。
  return <section className="session-info" aria-label="当前会话运行信息">
    {!connected ? <div className="session-info-notice offline" role="status">
      <strong>连接中断</strong><p>恢复连接后可继续使用。</p>
      {onOpenSettings && <button className="session-info-button primary" onClick={onOpenSettings}>打开连接设置</button>}
    </div> : needsInput ? <div className="session-info-notice waiting" role="status">
      <strong>等待确认</strong><p>请在输入框上方确认后继续。</p>
    </div> : !hasWork ? <p className="session-info-empty" role="status">{busy ? "正在处理请求，新的进展会显示在这里。" : "发送消息开始；有任务进展时会显示在这里。"}</p> : null}
    {plan.length > 0 && <section className="session-info-section">
      <h4>执行进度 <small aria-label={`已完成 ${done} / ${plan.length}`}>{done}/{plan.length}</small></h4>
      <ol className="session-info-steps">{plan.map((item, index) => {
        const tone = stepTone(item.status);
        // 已完成/待执行由图标表达，文字只给读屏；进行中与异常才把状态写出来，避免每行都挂一行灰字。
        const visible = tone === "active" || tone === "alert" || tone === "warn";
        return <li key={index} className={`session-step ${tone}`}>
          <span className="session-step-icon"><StepIcon tone={tone} /></span>
          <span className="session-step-text">{item.step}<small className={visible ? "" : "sr-only"}>{stepState(item.status)}</small></span>
        </li>;
      })}</ol>
    </section>}
    {turnSteps.length > 0 && <section className="session-info-section">
      <h4>本轮执行 <small>{[turnDuration > 0 ? formatActivityDuration(turnDuration) : "", turnTools > 0 ? `${turnTools} 个工具` : ""].filter(Boolean).join(" · ")}</small></h4>
      <ol className="session-info-steps">{turnSteps.map(item => {
        const tone = activityTone(item.status);
        const visible = tone === "active" || tone === "alert" || tone === "warn";
        return <li key={item.id} className={`session-step ${tone}`}>
          <span className="session-step-icon"><StepIcon tone={tone} /></span>
          <span className="session-step-text">{item.label}<small className={visible ? "" : "sr-only"}>{activityState(item.status)}</small></span>
          {item.durationMs != null && <time className="session-step-time">{formatActivityDuration(item.durationMs)}</time>}
        </li>;
      })}</ol>
    </section>}
    {/* 本轮执行列表已把失败项标红；只有在显示计划时才单列「需要关注」，避免同一件事出现两次 */}
    {plan.length > 0 && attention.length > 0 && <section className="session-info-section session-info-attention"><h4>需要关注</h4>
      <ul className="session-info-activities">{attention.slice(-8).map(item => <li key={item.id}><span>{item.label}</span><small>{activityState(item.status)}</small></li>)}</ul>
    </section>}
    {queued > 0 && <p className="session-info-queue">{queued} 条消息排队中</p>}
    <details className="session-info-details"><summary><ChevronRight size={14} aria-hidden="true" />会话详情</summary>
      <dl><dt>模型</dt><dd>{model || "自动选择"}</dd><dt>上下文</dt><dd><ContextStatus context={context} /></dd><dt>推理强度</dt><dd>服务默认 · 暂不可调</dd></dl>
      <button className="session-info-button" disabled={!connected} onClick={onRefresh}><RefreshCw size={13} aria-hidden="true" />刷新信息</button>
    </details>
  </section>;
}
