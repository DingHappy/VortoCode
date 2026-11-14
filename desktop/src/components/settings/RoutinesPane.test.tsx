import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { routineFixture } from '../../../qa/routine-fixture';
import { RoutinesView } from './RoutinesPane';

const actions = { onRefresh() {}, onChange() {}, onPreview() {} };
it('shows migration as a proposal, keeps project jobs deferred and warns when scheduling is off', () => {
  const html = renderToStaticMarkup(<RoutinesView state={{ value: routineFixture(), busy: false, error: '' }} {...actions} />);
  expect(html).toContain('尚未开启'); expect(html).toContain('项目提示类暂缓迁移'); expect(html).toContain('尚不生效');
  expect(html).toContain('已保存的默认时区'); expect(html).not.toContain('<script>');
});
it('makes retained stale content noninteractive after a failed read', () => {
  const html = renderToStaticMarkup(<RoutinesView state={{ value: routineFixture(), busy: false, error: '离线读取失败' }} {...actions} />);
  expect(html).toContain('role="alert"'); expect(html).toContain('disabled'); expect(html).toContain('已有内容保留');
});
it('marks interrupted migration as paused across both old and new scheduling', () => {
  const value = routineFixture(); value.migrations = [{ id: 'migration', status: 'applying' }];
  const html = renderToStaticMarkup(<RoutinesView state={{ value, busy: false, error: '' }} {...actions} />);
  expect(html).toContain('相关新旧作业均不调度'); expect(html).toContain('继续同一次迁移');
});

it('keeps failures, active work and explicit migration approval visible in the compact view', () => {
  const value = routineFixture();
  value.items[0].state.error = '需主人核对这条工作'; value.items[0].state.task = 'task-running';
  value.items[0].last_delivery!.status = 'failed';
  const html = renderToStaticMarkup(<RoutinesView state={{ value, busy: false, error: '' }} {...actions} />);
  expect(html).toContain('role="alert">需主人核对这条工作');
  expect(html).toContain('本条不会叠加派发');
  expect(html).toContain('已发出的请求仍可能返回');
  expect(html).toContain('主人核对并确认后才接管');
  expect(html).toContain('aria-label="搜索例行工作"');
  expect(html).toContain('role="status">最近任务：失败</span>');
  expect(html).not.toContain('已删除记录');
});
