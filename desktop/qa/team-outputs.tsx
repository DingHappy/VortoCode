// Real read-only components, synthetic data only. No API, model, native key or real file access.
import { useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { TeamDelivery } from '../src/components/TeamDelivery';
import type { TeamDetail, TeamOutput } from '../src/types';
import '../src/styles/tokens.css';
import '../src/styles/components.css';
import '../src/App.css';
import './team-outputs.css';

async function main() {
  document.documentElement.dataset.theme = 'light';
  const text = '公开证据🙂与出处。\n'.repeat(1200) + '【最后一条事实必须保留】\n<script>这里只是合成资料，不执行</script>';
  const characters = Array.from(text);
  const bytes = new TextEncoder().encode(text);
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n => n.toString(16).padStart(2, '0')).join('');
  const output: TeamOutput = { version: 1, task_id: 'task-0123456789', round: 2, sha256, characters: characters.length, bytes: bytes.length, tainted: true };
  const detail: TeamDetail = { id: 'task-abcdefghij', objective: '比较公开资料（合成）', status: 'review', mode: 'open', waiting_reason: '',
    used: {}, limits: { parallel: 2 }, project: null, members: [], tainted: true, events: [], acceptance: ['完整交回、附上来源'],
    submission: { summary: '合成总结：已交回材料，等主人核对。', tainted: true, evidence: [{ criterion: 0, evidence: '参见资料专员第 2 轮的完整文件' }] },
    children: [{ id: output.task_id, member: '资料专员', status: 'done', round: 2, review: 'accepted', tainted: true,
      result: '这是摘要，完整正文通过文件逐页读取。', output, outputs: [output] }] };
  function Fixture() {
    const [reads, setReads] = useState(0), [bad, setBad] = useState(false);
    const corrupt = useRef(false);
    const client = useMemo(() => ({
      async getTeam() { return structuredClone(detail); },
      async getTeamOutput(_team: string, _task: string, _round: number, offset: number) {
        setReads(value => value + 1);
        const end = Math.min(offset + 8000, characters.length);
        return { output: { ...output, sha256: corrupt.current && offset > 0 ? 'f'.repeat(64) : output.sha256 },
          text: characters.slice(offset, end).join(''), offset, next_offset: end < characters.length ? end : null,
          complete: end === characters.length, external_content: true };
      },
    }), []);
    return <main className="qa-output-page"><nav><span>离线夹具：不读真实文件，不调用模型；仅测试读取，不执行资料内容</span>
      <button className="vc-btn vc-btn--secondary" onClick={() => { corrupt.current = !corrupt.current; setBad(corrupt.current); }}>模拟下一页损坏：{bad ? '开启' : '关闭'}</button>
      <button className="vc-btn vc-btn--secondary" onClick={() => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'; }}>切换主题</button>
      <output aria-label="夹具读取次数">只读请求 {reads} 次 · 写入 0 次</output></nav>
      <TeamDelivery id={detail.id} client={client} connected initial={detail} /></main>;
  }
  createRoot(document.getElementById('root')!).render(<Fixture />);
}
void main();
