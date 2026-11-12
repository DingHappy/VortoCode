import { describe, it, expect, vi } from 'vitest';
import { recoveryController, type RecoveryClient, type RecoverySnapshot, type RecoveryState } from './staffRecovery';
function fixture() {
  const value: RecoverySnapshot = { instance: 'current', items: [{ id: 'task-one', digest: 'displayed', prompt: '核对', member: 'reader', team: null,
    round: 1, error: '中断', result: '', log: [], messages: [], usage: null, approval: {}, effects: '核对已生效的动作' }] };
  const client: RecoveryClient = { readStaffRecovery: vi.fn(async () => structuredClone(value)),
    decideStaffRecovery: vi.fn(async () => ({ results: [{ id: 'task-one', status: 'applied' }] })) };
  const states: RecoveryState[] = [];
  return { value, client, states, controller: recoveryController(client, next => states.push(next)) };
}
describe('owner batch reconciliation', () => {
  it('never writes on read; submits only the exact selected version after an owner action', async () => {
    const f = fixture(); await f.controller.refresh(); expect(f.client.decideStaffRecovery).not.toHaveBeenCalled();
    await f.controller.decide([{ id: 'task-one', digest: 'displayed' }], 'rework', 'current');
    expect(f.client.decideStaffRecovery).toHaveBeenCalledWith([{ id: 'task-one', digest: 'displayed' }], 'rework', 'current');
  });
  it.each(['instance', 'task'])('rejects a changed %s without writing', async changed => {
    const f = fixture(); await f.controller.refresh();
    if (changed === 'instance') f.value.instance = 'new'; else f.value.items[0].digest = 'new';
    await f.controller.decide([{ id: 'task-one', digest: 'displayed' }], 'abandon', 'current');
    expect(f.client.decideStaffRecovery).not.toHaveBeenCalled(); expect(f.states[f.states.length - 1]?.error).toContain('变化');
  });
  it('disposal during the fresh read prevents late recovery', async () => {
    const f = fixture(); await f.controller.refresh(); let finish!: (value: RecoverySnapshot) => void;
    vi.mocked(f.client.readStaffRecovery).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const pending = f.controller.decide([{ id: 'task-one', digest: 'displayed' }], 'rework', 'current');
    f.controller.dispose(); finish(f.value); await pending;
    expect(f.client.decideStaffRecovery).not.toHaveBeenCalled();
  });
  it('reports individual failures and does not automatically retry', async () => {
    const f = fixture(); await f.controller.refresh();
    vi.mocked(f.client.decideStaffRecovery).mockResolvedValueOnce({ results: [{ id: 'task-one', status: 'failed', error: '岗位已变化' }] });
    await f.controller.decide([{ id: 'task-one', digest: 'displayed' }], 'rework', 'current');
    expect(f.states[f.states.length - 1]?.notice).toContain('岗位已变化'); expect(f.client.decideStaffRecovery).toHaveBeenCalledTimes(1);
  });
});
