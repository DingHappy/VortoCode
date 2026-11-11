// Read-only synthetic data for the real settings view. No Gateway, model or key calls.
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { StaffView } from '../src/components/settings/StaffPane';
import type { StaffSnapshot } from '../src/lib/staff';
import '../src/styles/tokens.css';
import '../src/styles/components.css';
import '../src/App.css';
import './standing-staff.css';

const snapshot: StaffSnapshot = { roles: [], members: [], proposals: [],
  approval_key: { instance: 'offline-fixture', key: null },
  standing_specialists: [
    { id: 'calendar', title: '日程专员', scope: '只读日历，整理今日安排和时间冲突；不新建、改期、取消或回复邀请。',
      guide: '在主要聊天说明每天几点、使用哪个时区，提议日程专员整理今日安排。' },
    { id: 'mail', title: '邮件专员', scope: '读信、分类、列出待回复事项；存草稿逐次等主人批准，不发送，不联网。',
      guide: '在主要聊天说明每天两个汇报时间和时区，提议邮件专员整理未读摘要。' },
    { id: 'research', title: '资料专员', scope: '只查公开资料，不读取邮箱、日历、记忆或项目。',
      guide: '在主要聊天说明公开话题、每周哪天几点和时区，提议资料专员定期跟踪。' },
  ] };
document.documentElement.dataset.theme = 'light';
function Fixture() {
  const [reads, setReads] = useState(0), [offline, setOffline] = useState(false);
  return <main className="qa-standing-staff"><nav>
    <span>离线合成夹具：无真实资料、模型、钥匙或设置操作</span>
    <button onClick={() => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'; }}>切换主题</button>
    <button onClick={() => setOffline(value => !value)}>模拟连接变化</button>
    <output aria-label="夹具操作统计">刷新 {reads} 次 · 登记 0 次 · 批准 0 次</output>
  </nav><StaffView state={{ value: snapshot, busy: false, error: offline ? '合成连接已断开' : '' }}
    onRefresh={() => { setReads(n => n + 1); setOffline(false); }} onStatus={() => { throw new Error('夹具没有已登记专员'); }}
    onDecide={() => { throw new Error('夹具没有待批登记'); }} />
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
