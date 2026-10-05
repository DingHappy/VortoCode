import type { TaskChainBudget as Budget } from "../types";

export function TaskChainBudget({ budget }: { budget?: Budget }) {
  if (!budget || Object.keys(budget).length === 0) return null;
  if (budget.error) return <div className="task-chain-budget"><strong>任务链额度不可用</strong><p className="task-error">{budget.error}</p></div>;
  if (!budget.available || budget.mode !== "execution_allowance" || !budget.limits || !budget.used) return null;
  const { used, limits } = budget;
  return <section className="task-chain-budget">
    <strong title={budget.root_task_id}>任务链累计额度</strong>
    <p>任务 {used.tasks}/{limits.tasks} · 轮次 {used.rounds}/{limits.rounds}</p>
    <p>步骤配额 {used.steps}/{limits.steps} · 超时配额 {used.timeout_seconds}/{limits.timeout_seconds} 秒</p>
    <small>按每轮执行上限预留，失败或取消不返还。token／费用仍需独立计量。</small>
  </section>;
}
