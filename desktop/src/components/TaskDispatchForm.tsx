import type { useTaskDispatch } from "../hooks/useTaskDispatch";
import { statusLabel } from "../lib/labels";

type Props = {
  dispatch: ReturnType<typeof useTaskDispatch>;
  value: string;
  onChange: (value: string) => void;
  enabled: boolean;
};

export function TaskDispatchForm({ dispatch, value, onChange, enabled }: Props) {
  const { mode, setMode, agent, setAgent, criteria, setCriteria, maxSteps, setMaxSteps,
    capabilities, busy, error, setError, capabilityError, submit, requirements, setRequirements, dependencyCandidates,
    budgetEnabled, setBudgetEnabled, inheritedBudget } = dispatch;
  const roleUnavailable = Boolean(agent && capabilities && !capabilities.agents.some((role) => role.name === agent));
  return <div className="background-task-form task-dispatch-form">
    <label>任务类型<select value={mode} disabled={busy} onChange={(event) => { setMode(event.target.value as "dev" | "research"); setError(""); }}>
      <option value="dev">隔离开发 · 产出代码分支</option>
      <option value="research">只读研究 · 分析后提交结果</option>
    </select></label>
    <label>任务目标<textarea value={value} maxLength={mode === "research" ? 4000 : undefined} disabled={busy}
      onChange={(event) => onChange(event.target.value)}
      placeholder={mode === "research" ? "需要研究什么？期望获得什么结果？" : "交给隔离 worktree 后台执行…"} /></label>
    {mode === "research" && <>
      <label>执行角色<select value={agent} disabled={busy || !capabilities} onChange={(event) => setAgent(event.target.value)}>
        <option value="">研究员</option>
        {roleUnavailable && <option value={agent}>{agent} · 当前不可用，请重新选择</option>}
        {capabilities?.agents.map((role) => <option key={role.name} value={role.name}>{role.name} · {role.description}</option>)}
      </select></label>
      <label>验收要求<textarea value={criteria} disabled={busy} onChange={(event) => setCriteria(event.target.value)} placeholder="每行一条，例如：定位入口，提供文件证据，说明未验证内容" /></label>
      <label>执行步数上限<select value={maxSteps} disabled={busy} onChange={(event) => setMaxSteps(Number(event.target.value))}>
        {[4, 8, 12].map((value) => <option key={value} value={value}>{value} 步</option>)}
      </select></label>
      <small>只读取当前工作区，最多执行 5 分钟。结果提交后仍需验收。</small>
      {capabilities?.dependencies?.available && <details className="task-dependency-options"><summary>前置研究任务（可选） · 已选 {requirements.length}/8</summary>
        <p>选择当前会话的前置任务。前置结果验收后，核对并显式推进新任务。</p>
        {dependencyCandidates.slice(0, 20).map((task) => {
          const selected = requirements.some((ref) => ref.task_id === task.task_id && ref.round === task.round);
          return <label key={`${task.task_id}:${task.round}`} title={task.task_id}>
            <input type="checkbox" checked={selected} disabled={busy || (!selected && requirements.length >= 8)} onChange={() => {
              setRequirements(selected ? requirements.filter((ref) => ref.task_id !== task.task_id)
                : [...requirements.filter((ref) => ref.task_id !== task.task_id), { task_id: task.task_id, round: task.round }]);
            }} />
            <span>{task.prompt} · 第 {task.round} 轮 · {statusLabel(task.status)}</span>
          </label>;
        })}
        {dependencyCandidates.length === 0 && <p>当前会话暂无可选的研究任务。</p>}
        {requirements.length > 0 && <button type="button" disabled={busy} onClick={() => setRequirements([])}>清空前置任务</button>}
      </details>}
      {capabilities?.chain_budget?.available && <div className="task-chain-budget-options">
        <label><input type="checkbox" checked={budgetEnabled || inheritedBudget.roots.length > 0}
          disabled={busy || inheritedBudget.roots.length > 0} onChange={(event) => setBudgetEnabled(event.target.checked)} />
          <span>{inheritedBudget.roots.length ? "继承前置任务链的总额度" : "启用任务链总额度"}</span></label>
        <small>{inheritedBudget.roots.length ? "后续任务共享前置任务的剩余额度，不能重新设置。" :
          `覆盖本任务及继承子任务：${capabilities.chain_budget.defaults.tasks} 项任务、${capabilities.chain_budget.defaults.rounds} 轮、${capabilities.chain_budget.defaults.steps} 步配额、${Math.floor(capabilities.chain_budget.defaults.timeout_seconds / 60)} 分钟超时配额。`}
          失败和取消不返还预留额度；这里不封顶 token 或费用。</small>
      </div>}
      {capabilityError && <div role="alert" className="task-error">{capabilityError}</div>}
    </>}
    {error && <div role="alert" className="task-error">{error}</div>}
    <button disabled={!value.trim() || !enabled || busy || (mode === "research" && (!capabilities || roleUnavailable))} onClick={() => void submit()}>
      {busy ? "正在下派…" : mode === "research" ? "下派研究任务" : "后台开发"}
    </button>
  </div>;
}
