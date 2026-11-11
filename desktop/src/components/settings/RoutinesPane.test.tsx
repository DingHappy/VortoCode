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
