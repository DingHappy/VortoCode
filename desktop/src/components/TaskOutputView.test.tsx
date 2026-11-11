import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { TaskOutputView } from './TaskOutputView';
import type { TeamOutput } from '../types';
import { taskFeedItem } from '../lib/activityFeed';
import { teamRecord } from '../lib/teamAttention';
import type { TaskItem, TeamSummary } from '../types';

const output: TeamOutput = { version: 1, task_id: 'task-0123456789', round: 2, sha256: 'a'.repeat(64), characters: 20000, bytes: 50000, tainted: true };
it('shows stored size and an explicit unread state, without treating the preview as the full report', () => {
  const html = renderToStaticMarkup(<TaskOutputView team="team" output={output} client={{ getTeamOutput: async () => { throw new Error('unused'); } }} connected />);
  expect(html).toContain('20000'); expect(html).toContain('仍有内容未读取'); expect(html).toContain('读取交付文件');
  expect(html).not.toContain('全文已读取并核对摘要');
});
it('keeps reading disabled while disconnected or using an old client', () => {
  const html = renderToStaticMarkup(<TaskOutputView team="team" output={output} client={{}} connected={false} />);
  expect(html).toContain('disabled'); expect(html).toContain('尚未支持');
});
it('keeps a team member approval in the main attention inbox', () => {
  const task = { id: 'task-0123456789', kind: 'delegation', prompt: '起草目标', status: 'blocked', collaboration: {
    dispatch: { source: 'team', staff_roster: true }, approval: { status: 'pending', payload: { message: '先核对草稿' } }, messages: [] } } as unknown as TaskItem;
  const team = { id: 'team', objective: '整理', children: [{ id: task.id, member: '专员' }] } as TeamSummary;
  const item = teamRecord(taskFeedItem(task), [team]);
  expect(item.status).toBe('waiting'); expect(item.requiresReply).toBe(true); expect(item.summary).toContain('等你批准');
});
