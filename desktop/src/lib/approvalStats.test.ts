import { expect, it, vi } from 'vitest';
import { approvalStatsController, parseApprovalStats, type ApprovalStatsState } from './approvalStats';

import { sampleStats } from './approvalStats.fixture';
it('validates bounded categories and numbers instead of rendering unexpected payload fields', () => {
  expect(parseApprovalStats(sampleStats()).totals.count).toBe(2);
  for (const change of [{ kind: 'PRIVATE BODY' }, { count: -1 }, { median_wait_seconds: Infinity }, { tainted: 'PRIVATE BODY' }]) {
    const data = sampleStats(); Object.assign(data.groups[0], change);
    expect(() => parseApprovalStats(data)).toThrow();
  }
});
it('reads only the selected window, marks failed refresh stale, and ignores disposed results', async () => {
  const client = { readApprovalStats: vi.fn(async (_days: number) => sampleStats()) };
  const states: ApprovalStatsState[] = [];
  const control = approvalStatsController(client, state => states.push(state));
  await control.refresh(); expect(client.readApprovalStats).toHaveBeenCalledWith(7);
  expect(states[states.length - 1]?.value?.totals.count).toBe(2);
  client.readApprovalStats.mockRejectedValueOnce(new Error('PRIVATE BODY'));
  await control.refresh(1); expect(states[states.length - 1]?.error).toBeTruthy();
  expect(JSON.stringify(states)).not.toContain('PRIVATE BODY');
  let finish!: (value: ReturnType<typeof sampleStats>) => void;
  client.readApprovalStats.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const waiting = control.refresh(); control.dispose(); const size = states.length;
  finish(sampleStats()); await waiting; expect(states).toHaveLength(size);
});
