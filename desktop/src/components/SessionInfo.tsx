import { CircleCheck, CircleDot, CircleHelp } from "lucide-react";
import type { PlanItem, SessionContextUsage, TurnActivity } from "../types";
import { ContextStatus } from "./ContextStatus";
export { ContextStatus } from "./ContextStatus";

const activityState = (status: string) => ({ running: "进行中", completed: "已结束", succeeded: "已完成", failed: "失败", blocked: "等待处理", cancelled: "已取消", timed_out: "超时" } as Record<string, string>)[status] ?? "状态未知";
export function SessionInfo({ context, busy, connected, needsInput = false, plan, activities, queued, model, onRefresh, onOpenSettings }: {
  context?: SessionContextUsage; busy: boolean; connected: boolean; needsInput?: boolean; plan: PlanItem[];
  activities: TurnActivity[]; queued: number; model: string; onRefresh: () => void; onOpenSettings?: () => void;
}) {
  const hasWork = plan.length > 0 || activities.length > 0;
  const attention = activities.filter(item => ["failed", "blocked", "cancelled", "timed_out"].includes(item.status));
  return <section className="session-info" aria-label="当前会话运行信息">
    <div className="session-info-status" role="status">
      <h3>会话状态 <span>{!connected ? "连接中断" : needsInput ? "等待确认" : busy ? "运行中" : "就绪"}</span></h3>
      {!connected ? <><p>恢复连接后可继续使用。</p>{onOpenSettings && <button onClick={onOpenSettings}>打开连接设置</button>}</>
        : needsInput ? <p>请在输入框上方确认后继续。</p>
        : !hasWork ? <p>{busy ? "正在处理请求，新的进展会显示在这里。" : "发送消息开始；有任务进展时会显示在这里。"}</p> : null}
    </div>
    {plan.length > 0 && <section className="session-info-section">
      <h4>任务步骤 <small>{plan.filter(item => item.status === "completed").length}/{plan.length}</small></h4>
      <ol className="session-info-steps">{plan.map((item, index) => <li key={index}>
        {item.status === "completed" ? <CircleCheck size={15} /> : item.status === "in_progress" ? <CircleDot size={15} /> : <CircleHelp size={15} />}
        <span>{item.step}<small>{item.status === "completed" ? "已完成" : item.status === "in_progress" ? "进行中" : item.status === "pending" ? "待执行" : activityState(item.status)}</small></span>
      </li>)}</ol>
    </section>}
    {attention.length > 0 && <section className="session-info-section"><h4>需要关注</h4>
      <ul className="session-info-activities">{attention.slice(-8).map(item => <li key={item.id}><span>{item.label}</span><small>{activityState(item.status)}</small></li>)}</ul>
    </section>}
    {queued > 0 && <p className="session-info-queue">{queued} 条消息排队中</p>}
    <details className="session-info-details"><summary>会话详情</summary>
      <dl><dt>模型</dt><dd>{model || "自动选择"}</dd><dt>上下文</dt><dd><ContextStatus context={context} /></dd><dt>推理强度</dt><dd>服务默认 · 暂不可调</dd></dl>
      <button disabled={!connected} onClick={onRefresh}>刷新信息</button>
    </details>
  </section>;
}
