import type { RoutinesSnapshot } from '../src/lib/routines';

export function routineFixture(): RoutinesSnapshot {
  return { revision: 1, timezone: '', host_timezone: 'UTC', scheduler_enabled: false, migrations: [],
    items: [{ id: 'routine-example', name: '公开资料回顾（合成）', member: 'staff-public', member_name: '资料专员', role_title: '只读公开资料',
      prompt: '核对公开资料并附来源；这是离线合成数据。', acceptance: ['附来源'], delivery: 'notify', schedule: 'at 09:00', timezone: 'UTC',
      enabled: true, revision: 1, token_budget: 0, retired: false, legacy: null, next: '2026-11-12T09:00:00+00:00',
      state: { cursor: '2026-11-11T09:00:00+00:00', last_task: 'task-fixture', last_status: 'done' },
      last_delivery: { id: 'task-fixture', status: 'done', created: '2026-11-11T09:00:00+00:00', result: '合成资料：没有访问真实来源。<script>不执行</script>', error: '' } }],
    migration: { config_revision: 'a'.repeat(64), timezone: 'UTC', roles: [], members: [{ id: 'staff-old', name: '旧定时兼容专员', role: 'routine-public', model: 'offline-fixture' }],
      routines: [{ data: { id: 'routine-old', name: '旧公开作业（合成）', member: 'staff-old', prompt: '查询公开资料。仅为合成夹具，不调用模型。', acceptance: [],
        delivery: 'notify', schedule: 'every 1h', timezone: 'UTC', enabled: true, revision: 1, token_budget: 0 }, legacy: { announce: 'im', timeout: 3600, budget: 0 } }],
      deferred: [{ name: '旧项目作业（合成）', reason: '项目提示类暂缓迁移' }] } };
}
