import { useEffect, useRef, useState } from 'react';
import { confirmAction } from '../../lib/confirm';
import { recoveryController, type RecoveryAction, type RecoveryClient, type RecoveryState } from '../../lib/staffRecovery';

export function StaffRecovery({ client }: { client: RecoveryClient }) {
  const [state, setState] = useState<RecoveryState>({ value: null, busy: false, error: '' });
  const [selected, setSelected] = useState<string[]>([]);
  const controller = useRef<ReturnType<typeof recoveryController> | null>(null);
  useEffect(() => {
    const current = recoveryController(client, setState);
    controller.current = current; void current.refresh();
    return () => { current.dispose(); if (controller.current === current) controller.current = null; };
  }, [client]);
  useEffect(() => { setSelected([]); }, [state.value]);
  const disabled = state.busy || Boolean(state.error);
  function decide(action: RecoveryAction) {
    const current = controller.current, value = state.value;
    if (!current || !value) return;
    const rows = value.items.filter(item => selected.includes(item.id));
    void confirmAction(`${action === 'rework' ? '返工' : '放弃'}这 ${rows.length} 件中断任务？\n${rows.map(item => item.prompt).join('\n')}\n已经发出的动作可能生效，请先核对原系统。返工会创建新轮次，不恢复旧审批。`)
      .then(ok => { if (ok && current === controller.current) void current.decide(rows.map(({ id, digest }) => ({ id, digest })), action, value.instance); })
      .catch(() => setState(value => ({ ...value, error: '确认未完成，请刷新核对' })));
  }
  return <section className="staff-section" aria-label="中断任务核对">
    <div className="settings-card-head"><h3>中断任务核对</h3><button disabled={state.busy} onClick={() => void controller.current?.refresh()}>刷新</button></div>
    <p className="settings-card-note">只有持久记录证明从未开始的排队任务会自动重排。下面的任务需要你核对已执行步骤和可能生效的动作。</p>
    {state.error && <p role="alert">{state.error}。保留上次记录，刷新后才能操作。</p>}
    {state.notice && <p role="status">{state.notice}</p>}
    {!state.value && !state.error && <p role="status">正在核对…</p>}
    {state.value?.items.map(item => <article className="settings-card" key={item.id}>
      <label><input type="checkbox" disabled={disabled} checked={selected.includes(item.id)} onChange={event => setSelected(ids => event.target.checked ? [...ids, item.id] : ids.filter(id => id !== item.id))} />{item.prompt}</label>
      <p>{item.member} · 第 {item.round} 轮{item.team ? ` · 所属小组 ${item.team}` : ''}</p>
      <p>{item.error || '重启前执行情况待核对'}</p><p className="settings-card-note">{item.effects}</p>
      <details><summary>执行记录与可能生效的动作</summary><pre className="staff-record">{JSON.stringify({ progress: item.log, messages: item.messages, usage: item.usage, approval: item.approval, execution: item.execution, result: item.result }, null, 2)}</pre></details>
    </article>)}
    {state.value && !state.value.items.length && <p>没有待核对的中断专员任务。</p>}
    <div className="settings-card-actions"><button disabled={disabled || !selected.length} onClick={() => decide('abandon')}>放弃所选</button><button disabled={disabled || !selected.length} onClick={() => decide('rework')}>核对后返工所选</button></div>
  </section>;
}
