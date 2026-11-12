import { useEffect, useRef, useState } from 'react';
import { confirmAction } from '../../lib/confirm';
import { routinesController, scheduleProposal, type RoutinesClient, type RoutinesState, type StaffRoutine } from '../../lib/routines';
import { ResultDocument } from '../ResultDocument';
import './RoutinesPane.css';

export function RoutinesView({ state, onRefresh, onChange, onPreview }: {
  state: RoutinesState; onRefresh: () => void; onChange: (action: string, data: Record<string, unknown>) => void;
  onPreview: (timezone: string) => void;
}) {
  const { value, busy, error } = state;
  const [timezone, setTimezone] = useState('');
  const [migrationZone, setMigrationZone] = useState('');
  const [editing, setEditing] = useState<StaffRoutine | null>(null);
  const [schedule, setSchedule] = useState(''), [scheduleZone, setScheduleZone] = useState('');
  useEffect(() => {
    if (value && !timezone) setTimezone(value.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
    if (value?.host_timezone && !migrationZone) setMigrationZone(value.host_timezone);
  }, [value, timezone, migrationZone]);
  const disabled = busy || Boolean(error), plan = value?.migration;
  return <section className="routines-pane" aria-label="例行工作">
    <div className="settings-card-head"><h3>例行工作</h3><button disabled={busy} onClick={onRefresh}>刷新</button></div>
    <p className="settings-card-note">到点把一件事交给已批准的专员。权限来自岗位，需要确认的动作仍等你批准；自动审核保持关闭。</p>
    {error && <p role="alert">{error}。已有内容保留，刷新核对后才能操作。</p>}
    {state.notice && <p role="status">{state.notice}</p>}
    {!value && <p role="status">{busy ? '正在读取…' : '尚未核对例行工作。'}</p>}
    {value && <>
      {!value.scheduler_enabled && <p className="settings-card-note">这台电脑的定时调度尚未开启，已登记的事项不会自动运行。</p>}
      <div className="settings-card">
        <p>已保存的默认时区：{value.timezone || '尚未设置'}</p>
        <label>新例行工作的默认时区<input value={timezone} disabled={disabled} onChange={event => setTimezone(event.target.value)} placeholder="例如 Asia/Shanghai" /></label>
        <p className="settings-card-note">首次建议来自本机系统，保存前请核对。只影响以后新建的例行工作，已有排期、早晚报和其它时钟保持原规则。</p>
        <button disabled={disabled || !timezone.trim()} onClick={() => onChange('timezone', { timezone: timezone.trim(), revision: value.revision })}>确认保存默认时区</button>
      </div>
      {value.migrations.some(item => item.status === 'applying') && <p role="alert">有一批迁移已有批准、登记尚待核对；相关新旧作业均不调度，请到待批登记继续同一次迁移。</p>}
      <details className="settings-card">
        <summary>迁移旧定时事项</summary>
        <p>先预览，再提交提议；由主人核对并确认后才接管。离线和联网分别保留原工具范围，项目及命令作业暂留原路径。原配置作为证据保留，不会同时执行两份。</p>
        <label>旧主机原时区<input value={migrationZone} disabled={disabled} onChange={event => setMigrationZone(event.target.value)} placeholder="无法识别时请明确填写" /></label>
        <button disabled={disabled || !migrationZone.trim()} onClick={() => onPreview(migrationZone.trim())}>预览迁移清单</button>
        {plan && <>
          <p>已核对的原时区：{plan.timezone} · 可迁移 {plan.routines.length} 条</p>
          {plan.routines.map(entry => <article key={entry.data.id}>
            <strong>{entry.data.name}</strong><p>{entry.data.schedule} · {entry.data.timezone} · {entry.legacy.announce === 'im' ? '推送结果' : '成功时静默，失败仍通知'}</p>
            <p>模型：{value.migration?.members.find(member => member.id === entry.data.member)?.model || '默认模型'} · 总时限 {entry.legacy.timeout} 秒 · 任务额度 {entry.data.token_budget || '不限'}</p>
            <details><summary>完整任务原文</summary><pre className="staff-record">{entry.data.prompt}</pre></details>
          </article>)}
          {plan.deferred.map(item => <p key={item.name}>{item.name}：{item.reason}</p>)}
          <button disabled={disabled || !plan.routines.length || plan.timezone !== migrationZone.trim()} onClick={() => onChange('migration', {
            definition: { timezone: plan.timezone, config_revision: plan.config_revision }, reason: '按已核对清单迁移旧提示类定时作业，权限分别保留。',
          })}>提交迁移提议（尚不生效）</button>
        </>}
      </details>
      {value.items.filter(item => !item.retired).map(item => <article className="settings-card" key={item.id}>
        <div className="settings-card-head"><strong>{item.name}</strong><span>{item.enabled ? '已启用' : '已暂停'}</span></div>
        <p>{item.member_name} · {item.role_title} · {item.delivery === 'review' ? '要主人验收' : '只告知'}</p>
        <p>{item.schedule} · {item.timezone}{item.next ? ` · 下次 ${item.next}` : ' · 暂无下一次执行'}</p>
        {item.state.error && <p role="alert">{item.state.error}</p>}
        {item.state.task && <p>原任务仍在执行或等待处理，本条不会叠加派发。</p>}
        {item.last_delivery && <><p>最近任务：{item.last_delivery.status} · {item.last_delivery.id}</p>
          <ResultDocument title="最近保存的结果摘要（可能非全文）" text={item.last_delivery.result || item.last_delivery.error || '暂无正文'} tainted /></>}
        <div className="settings-card-actions">
          <button disabled={disabled} onClick={() => onChange('status', { id: item.id, revision: item.revision, enabled: !item.enabled })}>{item.enabled ? '暂停排期' : '核对后恢复'}</button>
          <button disabled={disabled} onClick={() => { setEditing(item); setSchedule(item.schedule); setScheduleZone(item.timezone); }}>提议改时间</button>
          <button disabled={disabled} onClick={() => onChange('delete', { id: item.id, revision: item.revision })}>删除例行工作</button>
        </div>
        <small>暂停或删除会阻止旧任务继续下一步；已发出的请求仍可能返回。执行历史保留。</small>
      </article>)}
      {!value.items.some(item => !item.retired) && <p>还没有例行工作。可以在主要聊天里请助理用已登记专员提议一条。</p>}
      {editing && <form className="settings-card" onSubmit={event => { event.preventDefault(); onChange('propose', {
        definition: scheduleProposal(editing, schedule.trim(), scheduleZone.trim()), reason: '主人核对后提议调整例行工作的时间。',
      }); setEditing(null); }}>
        <h4>提议调整「{editing.name}」</h4>
        <label>排期<input value={schedule} disabled={disabled} onChange={event => setSchedule(event.target.value)} /></label>
        <label>时区<input value={scheduleZone} disabled={disabled} onChange={event => setScheduleZone(event.target.value)} /></label>
        <p>支持 at 09:00、every 1h、五段式；一次性时间用 once 加带时区偏移的日期时间。提交后仍待批准。</p>
        <button disabled={disabled || !schedule.trim() || !scheduleZone.trim()}>提交时间提议</button>
        <button type="button" onClick={() => setEditing(null)}>取消</button>
      </form>}
      {value.items.some(item => item.retired) && <details><summary>已删除记录 · {value.items.filter(item => item.retired).length}</summary>
        {value.items.filter(item => item.retired).map(item => <p key={item.id}>{item.name} · 历史保留，旧配置不会自动恢复。</p>)}
      </details>}
    </>}
  </section>;
}

export function RoutinesPane({ client, onProposed, confirm = confirmAction }: {
  client: RoutinesClient; onProposed: () => void; confirm?: (message: string) => Promise<boolean>;
}) {
  const [state, setState] = useState<RoutinesState>({ value: null, busy: false, error: '' });
  const controller = useRef<ReturnType<typeof routinesController> | null>(null);
  const proposed = useRef(onProposed); proposed.current = onProposed;
  useEffect(() => {
    const current = routinesController(client, setState, () => proposed.current());
    controller.current = current; void current.refresh();
    return () => { current.dispose(); if (controller.current === current) controller.current = null; };
  }, [client]);
  return <RoutinesView state={state} onRefresh={() => void controller.current?.refresh()}
    onPreview={timezone => void controller.current?.refresh(timezone)} onChange={(action, data) => {
      const current = controller.current;
      if (action !== 'delete') { void current?.change(action, data); return; }
      void confirm('删除这条例行工作？执行历史保留，不再按排期派发，旧配置不会自动恢复。')
        .then(ok => { if (ok === true && current === controller.current) void current?.change(action, data); })
        .catch(() => { if (current === controller.current) setState(value => ({ ...value, error: '确认未完成，请刷新核对' })); });
    }} />;
}
