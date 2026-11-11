import { expect, it } from 'vitest';
import { routineFixture } from '../../qa/routine-fixture';
import { routinesController, scheduleProposal, type RoutinesState } from './routines';

function setup() {
  let snapshot = routineFixture();
  let state: RoutinesState = { value: null, busy: false, error: '' };
  const writes: Array<{ action: string; data: Record<string, unknown> }> = [], proposed: string[] = [];
  const client = { readRoutines: async () => structuredClone(snapshot), changeRoutines: async (action: string, data: Record<string, unknown>) => { writes.push({ action, data }); } };
  const controller = routinesController(client, next => { state = next; }, () => proposed.push('pending'));
  return { client, controller, writes, proposed, state: () => state, replace: (next: typeof snapshot) => { snapshot = next; } };
}
it('reads without writing timezone, granting permissions or starting migration', async () => {
  const h = setup(); await h.controller.refresh();
  expect(h.state().value?.timezone).toBe(''); expect(h.writes).toEqual([]);
});
it('time edits contain task fields and no authority or migration fields', () => {
  const definition = scheduleProposal(routineFixture().items[0], 'at 10:00', 'UTC');
  expect(definition.schedule).toBe('at 10:00'); expect(definition.revision).toBe(1);
  expect(Object.keys(definition)).not.toContain('grants'); expect(Object.keys(definition)).not.toContain('legacy');
  expect(Object.keys(definition)).not.toContain('binding');
});
it('refuses a stale version before sending a status mutation', async () => {
  const h = setup(); await h.controller.refresh(); const changed = routineFixture(); changed.items[0].revision = 2; h.replace(changed);
  await h.controller.change('status', { id: 'routine-example', revision: 1, enabled: false });
  expect(h.writes).toEqual([]); expect(h.state().error).toContain('变化');
});
it('refuses a changed migration source', async () => {
  const h = setup(); await h.controller.refresh('UTC'); const changed = routineFixture(); changed.migration!.config_revision = 'b'.repeat(64); h.replace(changed);
  await h.controller.change('migration', { definition: { timezone: 'UTC', config_revision: 'a'.repeat(64) }, reason: '迁移' });
  expect(h.writes).toEqual([]); expect(h.state().error).toContain('重新预览');
});
it('reports a proposal as pending rather than registering it', async () => {
  const h = setup(); await h.controller.refresh();
  await h.controller.change('propose', { definition: scheduleProposal(routineFixture().items[0], 'at 10:00', 'UTC'), reason: '改时间' });
  expect(h.writes.map(w => w.action)).toEqual(['propose']); expect(h.proposed).toEqual(['pending']);
  expect(h.state().notice).toContain('尚未生效'); expect(h.state().value?.items[0].schedule).toBe('at 09:00');
});
it('disposes an old computer while its fresh read is pending, without sending a write', async () => {
  const h = setup(); await h.controller.refresh();
  let finish!: (value: ReturnType<typeof routineFixture>) => void;
  h.client.readRoutines = () => new Promise(resolve => { finish = resolve; });
  const changing = h.controller.change('timezone', { timezone: 'UTC', revision: 1 });
  h.controller.dispose(); finish(routineFixture()); await changing;
  expect(h.writes).toEqual([]);
});
it('keeps failed readback uncertain after a successful server write', async () => {
  const h = setup(); await h.controller.refresh(); let reads = 0;
  h.client.readRoutines = async () => { if (++reads > 1) throw new Error('读取结果失败'); return routineFixture(); };
  await h.controller.change('timezone', { timezone: 'UTC', revision: 1 });
  expect(h.writes).toHaveLength(1); expect(h.state().error).toContain('失败'); expect(h.state().notice).toBe('');
});
