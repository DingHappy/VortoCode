import { expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ApprovalStatsView } from './ApprovalStatsPane';
import { sampleStats } from '../../lib/approvalStats.fixture';
import { parseApprovalStats } from '../../lib/approvalStats';

it('renders read-only daily categories and separate automatic bases', () => {
  const html = renderToStaticMarkup(<ApprovalStatsView state={{ value: parseApprovalStats(sampleStats()), busy: false, error: '' }} onRefresh={() => {}} />);
  for (const text of ['审批统计', '主要聊天', '中位等待', '自动放行', 'UTC', '5 秒']) expect(html).toContain(text);
  expect(html).not.toContain('批准登记'); expect(html).not.toContain('type="submit"');
});
it('keeps missing history and failed refresh visible instead of showing a complete zero', () => {
  const data = sampleStats(); data.coverage.audit = 'partial'; data.coverage.missing_wait = 2;
  const html = renderToStaticMarkup(<ApprovalStatsView state={{ value: parseApprovalStats(data), busy: false, error: '统计读取失败' }} onRefresh={() => {}} />);
  expect(html).toContain('上次读取'); expect(html).toContain('记录不完整'); expect(html).toContain('缺少等待时长');
});
it('uses existing registry names for source categories and escapes them as text', () => {
  const data = sampleStats(); data.groups[0].source = 'staff'; data.groups[0].source_id = 'staff-reader';
  const html = renderToStaticMarkup(<ApprovalStatsView state={{ value: parseApprovalStats(data), busy: false, error: '' }}
    names={{ 'staff-reader': '资料专员 <script>fixture</script>' }} onRefresh={() => {}} />);
  expect(html).toContain('资料专员'); expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('<script>');
});
