import { ChevronRight, Circle, CircleAlert, CircleCheck, LoaderCircle, RefreshCw } from "lucide-react";
import type { PlanItem, SessionContextUsage, TurnActivity } from "../types";
import { ContextStatus } from "./ContextStatus";
export { ContextStatus } from "./ContextStatus";

const activityState = (status: string) => ({ running: "进行中", completed: "已结束", succeeded: "已完成", failed: "失败", blocked: "等待处理", cancelled: "已取消", timed_out: "超时" } as Record<string, string>)[status] ?? "状态未知";
const stepState = (status: string) => status === "completed" ? "已完成" : status === "in_progress" ? "进行中" : status === "pending" ? "待执行" : activityState(status);
/** 步骤的视觉档：完成 / 进行中 / 待执行 / 出错（失败、超时、未知状态）/ 等待（阻塞、取消）。 */
const stepTone = (status: string) => status === "completed" ? "done" : status === "in_progress" ? "active" : status === "pending" ? "pending"
  : status === "blocked" || status === "cancelled" ? "warn" : "alert";
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
  const state = !connected ? { tone: "offline", text: "连接中断" } : needsInput ? { tone: "waiting", text: "等待确认" } : busy ? { tone: "running", text: "运行中" } : { tone: "ready", text: "就绪" };
  return <section className="session-info" aria-label="当前会话运行信息">
    <div className="session-info-status" role="status">
      <h3>会话状态 <span className={`session-state-pill ${state.tone}`}>{state.text}</span></h3>
      {!connected ? <><p>恢复连接后可继续使用。</p>{onOpenSettings && <button className="session-info-button primary" onClick={onOpenSettings}>打开连接设置</button>}</>
        : needsInput ? <p>请在输入框上方确认后继续。</p>
        : !hasWork ? <p>{busy ? "正在处理请求，新的进展会显示在这里。" : "发送消息开始；有任务进展时会显示在这里。"}</p> : null}
    </div>
    {plan.length > 0 && <section className="session-info-section">
      <h4>执行进度 <small>{done}/{plan.length}</small></h4>
      <div className="session-progress" role="progressbar" aria-label="执行进度" aria-valuemin={0} aria-valuemax={plan.length} aria-valuenow={done}>
        <span style={{ width: `${Math.round((done / plan.length) * 100)}%` }} />
      </div>
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
    {attention.length > 0 && <section className="session-info-section session-info-attention"><h4>需要关注</h4>
      <ul className="session-info-activities">{attention.slice(-8).map(item => <li key={item.id}><span>{item.label}</span><small>{activityState(item.status)}</small></li>)}</ul>
    </section>}
    {queued > 0 && <p className="session-info-queue">{queued} 条消息排队中</p>}
    <details className="session-info-details"><summary><ChevronRight size={14} aria-hidden="true" />会话详情</summary>
      <dl><dt>模型</dt><dd>{model || "自动选择"}</dd><dt>上下文</dt><dd><ContextStatus context={context} /></dd><dt>推理强度</dt><dd>服务默认 · 暂不可调</dd></dl>
      <button className="session-info-button" disabled={!connected} onClick={onRefresh}><RefreshCw size={13} aria-hidden="true" />刷新信息</button>
    </details>
  </section>;
}
