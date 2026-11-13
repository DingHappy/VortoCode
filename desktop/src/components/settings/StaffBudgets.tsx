import { useEffect, useId, useState } from "react";
import type { StaffBudgetSettings, StaffUsage } from "../../lib/staff";

export function StaffBudgets({ value, usage, disabled, onSave }: { value: StaffBudgetSettings; usage?: StaffUsage; disabled: boolean;
  onSave?: (settings: StaffBudgetSettings) => void }) {
  const [daily, setDaily] = useState(String(value.daily_tokens)), [task, setTask] = useState(String(value.task_tokens));
  useEffect(() => { setDaily(String(value.daily_tokens)); setTask(String(value.task_tokens)); }, [value.revision]);
  const id = useId();
  const valid = [daily, task].every(text => /^\d+$/.test(text) && Number.isSafeInteger(Number(text)));
  return <section className="settings-card staff-budget-settings" aria-label="专员后台额度">
    <h3>专员后台额度</h3><p>{usage?.error || (typeof usage?.total === 'number' && Number.isFinite(usage.total) && usage.total >= 0 ? `今天已记录 ${usage.total} token` : '用量尚未核对')}</p>
    <label htmlFor={`${id}-daily`}>全局每日 token 上限（0 不限）</label><input id={`${id}-daily`} inputMode="numeric" value={daily} disabled={disabled} onChange={event => setDaily(event.target.value)} />
    <label htmlFor={`${id}-task`}>单任务累计 token 上限（0 不限）</label><input id={`${id}-task`} inputMode="numeric" value={task} disabled={disabled} onChange={event => setTask(event.target.value)} />
    <p className="settings-card-note">达到额度后暂停后续请求；已在途请求可能超出剩余额度。每日用量沿用服务端本地日期，token 用量不等于账单金额。旧定时作业将在迁移后接入。</p>
    <button className="vc-btn vc-btn--primary" disabled={disabled || !valid || !onSave} onClick={() => onSave?.({ revision: value.revision, daily_tokens: Number(daily), task_tokens: Number(task) })}>保存专员额度</button>
  </section>;
}
