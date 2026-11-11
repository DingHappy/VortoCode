import { useEffect, useRef, useState } from 'react';
import type { TeamOutput } from '../types';
import { appendOutputPage, type TeamOutputClient } from '../lib/teamOutputs';
import { ResultDocument } from './ResultDocument';

export function TaskOutputView({ team, output, client, connected }: {
  team: string; output: TeamOutput; client: Partial<TeamOutputClient>; connected: boolean;
}) {
  const key = `${team}:${output.task_id}:${output.round}:${output.sha256}`;
  const current = useRef({ client, key, connected }); current.current = { client, key, connected };
  const [state, setState] = useState({ client, key, text: '', next: 0 as number | null, complete: false });
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const live = useRef(true), pending = useRef<symbol | null>(null);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => { pending.current = null; setBusy(false); setError(''); }, [client, key]);
  const value = state.client === client && state.key === key ? state : { client, key, text: '', next: 0, complete: false };
  const read = async () => {
    if (pending.current || !connected || !client.getTeamOutput || value.next === null) return;
    const request = Symbol(); pending.current = request; setBusy(true); setError('');
    const fresh = () => live.current && current.current.client === client && current.current.key === key && current.current.connected;
    try {
      const result = await appendOutputPage(client as TeamOutputClient, team, output, value.text, value.next, fresh);
      if (fresh() && result) setState({ client, key, ...result });
    } catch (reason) { if (fresh()) setError(reason instanceof Error ? reason.message : '完整交付暂不可读取'); }
    finally { if (pending.current === request) { pending.current = null; if (live.current) setBusy(false); } }
  };
  return <section className="task-output" aria-label="完整交付文件">
    <p>第 {output.round} 轮 · 已保存 {output.characters} 字 · {value.complete ? '全文已读取并核对摘要' : `已读取 ${Array.from(value.text).length} 字，仍有内容未读取`}</p>
    {!value.complete && <button className="vc-btn vc-btn--secondary" disabled={!connected || busy || !client.getTeamOutput} onClick={() => void read()}>
      {busy ? '正在读取…' : value.next === 0 ? '读取交付文件' : '继续读取下一页'}</button>}
    {!client.getTeamOutput && <p>当前连接尚未支持交付文件读取。</p>}
    {value.text && <ResultDocument title={value.complete ? '查看完整交付正文' : '查看已读取部分（尚未读完）'} text={value.text} tainted />}
    {error && <p role="alert">{error}。已读取部分保留，不代表完整正文。</p>}
  </section>;
}
