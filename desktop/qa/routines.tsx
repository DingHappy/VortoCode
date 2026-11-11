// Real controller and components; all reads, writes and confirmations stay in this synthetic fixture.
import { useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { RoutinesPane } from '../src/components/settings/RoutinesPane';
import { routineFixture } from './routine-fixture';
import '../src/styles/tokens.css';
import '../src/styles/components.css';
import '../src/components/settings/StaffPane.css';
import '../src/App.css';
import './routines.css';

document.documentElement.dataset.theme = 'light';
function Fixture() {
  const current = useRef(routineFixture());
  const [reads, setReads] = useState(0), [writes, setWrites] = useState(0), [proposals, setProposals] = useState(0);
  const [dialog, setDialog] = useState<{ text: string; finish: (answer: boolean) => void } | null>(null);
  const client = useMemo(() => ({
    async readRoutines(timezone = '') {
      setReads(n => n + 1);
      const result = structuredClone(current.current);
      if (!timezone) result.migration = null;
      else if (timezone !== 'UTC') throw new Error('离线夹具只提供 UTC 合成清单');
      return result;
    },
    async changeRoutines(action: string, data: Record<string, unknown>) {
      setWrites(n => n + 1);
      if (action === 'timezone') { current.current.timezone = String(data.timezone); current.current.revision++; }
      if (action === 'status' || action === 'delete') {
        const item = current.current.items.find(x => x.id === data.id);
        if (!item || item.revision !== data.revision) throw new Error('合成记录已变化');
        item.revision++; item.enabled = action === 'status' && data.enabled === true; item.retired = action === 'delete';
      }
      // Proposals intentionally grant nothing and never alter a schedule.
    },
  }), []);
  return <main className="qa-routines"><nav>
    <span>离线合成夹具：不访问真实文件、模型、钥匙或设置</span>
    <button onClick={() => { current.current.migration!.config_revision = 'b'.repeat(64); }}>模拟原配置变化</button>
    <button onClick={() => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; }}>切换主题</button>
    <output aria-label="夹具请求统计">读取 {reads} 次 · 写入 {writes} 次 · 待批提议 {proposals} 个（未生效）</output>
  </nav>
    <RoutinesPane client={client} onProposed={() => setProposals(n => n + 1)}
      confirm={text => new Promise(resolve => setDialog({ text, finish: resolve }))} />
    {dialog && <section role="dialog" aria-label="合成删除确认"><p>{dialog.text}</p>
      <button onClick={() => { dialog.finish(false); setDialog(null); }}>取消删除</button>
      <button onClick={() => { dialog.finish(true); setDialog(null); }}>确认删除（仅夹具）</button>
    </section>}
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
