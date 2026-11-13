import { useEffect, useRef, useState } from 'react';
import { approvalCategory } from '../../lib/approval';
import { approvalStatsController, type ApprovalStatsClient, type ApprovalStatsState } from '../../lib/approvalStats';
import './ApprovalStatsPane.css';

const sources: Record<string, string> = { main: '主要聊天', staff: '专员', routine: '例行工作', registration: '登记提议', other: '其它或旧来源' };
const bases: Record<string, string> = { existing_trust: '授权档位', turn: '本轮同类', task: '任务内同类', standing: '岗位常设', task_workspace: '任务副本', protective: '保护性操作', unknown: '依据未知' };
const wait = (seconds: number | null) => seconds === null ? '未知' : `${Math.round(seconds)} 秒`;
export function ApprovalStatsView({ state, onRefresh, names = {} }: { state: ApprovalStatsState; onRefresh: (days?: number) => void; names?: Record<string, string> }) {
  const { value, busy, error } = state;
  return <section className="approval-stats staff-section" aria-label="审批统计">
    <div className="settings-card-head"><strong>审批统计</strong><button disabled={busy} onClick={() => onRefresh()}>{busy ? '正在读取…' : '刷新统计'}</button></div>
    <label className="staff-field">统计范围<select aria-label="统计范围" disabled={busy} value={value?.days ?? 7} onChange={event => onRefresh(Number(event.target.value))}>
      <option value={1}>最近 1 天</option><option value={7}>最近 7 天</option><option value={30}>最近 30 天</option>
    </select></label>
    {error && <p role="alert">{error}。{value ? '以下为上次读取的数据。' : '目前无法确认统计。'}</p>}
    {!value && !error && <p role="status">正在读取审批记录…</p>}
    {value && <>
      <p className="settings-card-note">按 UTC 自然日汇总，截至 {value.to}。只读统计，不改变任何放行规则。</p>
      {(value.coverage.audit !== 'current' || value.coverage.tasks !== 'current') && <p role="alert">记录不完整，以下仅为已读取的部分，不能代表整个时间范围。</p>}
      <p>确认记录 {value.totals.count} 次 · 批准 {value.totals.approved} · 拒绝 {value.totals.denied} · 过期 {value.totals.expired} · 等待 {value.totals.pending}</p>
      <p className="settings-card-note">取消 {value.totals.cancelled} · 中断 {value.totals.interrupted} · 结果未知 {value.totals.unknown} · 中位等待 {wait(value.totals.median_wait_seconds)}（{value.totals.timed} 次有时长）</p>
      {(value.coverage.missing_wait > 0 || value.coverage.unknown_metadata > 0 || value.coverage.invalid_timestamps > 0) && <p className="settings-card-note">缺少等待时长 {value.coverage.missing_wait} 条；来源、类别或结果未知 {value.coverage.unknown_metadata} 条；无法归日 {value.coverage.invalid_timestamps} 条。旧记录不补算成零。</p>}
      {value.groups.length ? <div className="approval-stats-table"><table>
        <caption>每日人工确认记录；中断、取消和未知结果计入「其它」</caption>
        <thead><tr>{['日期', '来源', '类别', '污点', '次数', '批准', '拒绝', '过期', '等待', '其它', '中位等待'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{value.groups.map((row, index) => <tr key={index}>
          <td>{row.day}</td><td>{sources[row.source]}{row.source_id && <> · {names[row.source_id] ?? row.source_id}</>}</td>
          <td>{approvalCategory(row.kind)}</td><td>{row.tainted === 'unknown' ? '未知' : row.tainted ? '有' : '无'}</td>
          <td>{row.count}</td><td>{row.approved}</td><td>{row.denied}</td><td>{row.expired}</td><td>{row.pending}</td>
          <td>{row.cancelled + row.interrupted + row.unknown}</td><td>{wait(row.median_wait_seconds)}</td>
        </tr>)}</tbody>
      </table></div> : <p>已读取的记录中，这段时间没有人工确认。可在处理审批后刷新核对。</p>}
      <details><summary>自动放行 · {Object.values(value.automatic).reduce((n, count) => n + count, 0)} 次</summary>
        <dl className="staff-grants">{Object.entries(value.automatic).map(([basis, count]) => <div className="approval-stats-basis" key={basis}><dt>{bases[basis]}</dt><dd>{count}</dd></div>)}</dl>
        <p className="settings-card-note">系统拒绝 {value.system_denied} 次，未计作主人拒绝；未开放的放行类别保持为零。</p>
      </details>
    </>}
  </section>;
}
function LoadedStats({ client, names }: { client: ApprovalStatsClient; names: Record<string, string> }) {
  const [state, setState] = useState<ApprovalStatsState>({ value: null, busy: false, error: '' });
  const control = useRef<ReturnType<typeof approvalStatsController> | null>(null);
  useEffect(() => {
    setState({ value: null, busy: false, error: '' });
    const current = approvalStatsController(client, setState);
    control.current = current; void current.refresh();
    return () => { current.dispose(); if (control.current === current) control.current = null; };
  }, [client]);
  return <ApprovalStatsView state={state} names={names} onRefresh={days => void control.current?.refresh(days)} />;
}
export function ApprovalStatsPane({ client, names = {} }: { client: ApprovalStatsClient; names?: Record<string, string> }) {
  const [opened, setOpened] = useState(false);
  return <details className="staff-section" onToggle={event => setOpened(event.currentTarget.open)}><summary>审批统计（只读）</summary>
    {opened && <LoadedStats client={client} names={names} />}
  </details>;
}
