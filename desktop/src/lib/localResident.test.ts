import { describe, expect, it, vi } from 'vitest';
import { localResidentController, type LocalResidentClient, type LocalResidentState, type LocalResidentStatus } from './localResident';
import type { DesktopProjectProfile } from '../types';

const status = (patch: Partial<LocalResidentStatus> = {}): LocalResidentStatus => ({ supported: true, installed: true,
  state: 'running', availableVersion: '0.2.60', version: '0.2.60', cronEnabled: false, revision: 'rev-one', launchId: 'launch-one', message: '', ...patch });
const profile = { id: 'local-host', kind: 'remote', repoRoot: '@assistant', name: '本机常驻助理', baseUrl: 'http://127.0.0.1:8088', lastOpenedAt: 0 } as DesktopProjectProfile;
function fixture(initial = status()) {
  const value = { current: initial };
  const client: LocalResidentClient = { read: vi.fn(async () => structuredClone(value.current)),
    install: vi.fn(async () => status({ state: 'stopped' })), start: vi.fn(async () => status()),
    stop: vi.fn(async () => status({ state: 'stopped', launchId: undefined })), update: vi.fn(async () => status()),
    applyModel: vi.fn(async () => status()), connect: vi.fn(async () => profile) };
  const states: LocalResidentState[] = []; const onUse = vi.fn(async () => {}); const onStatus = vi.fn();
  const controller = localResidentController(client, next => states.push(next), onUse, onStatus);
  return { value, client, states, onUse, onStatus, controller, last: () => states[states.length - 1] };
}
describe('local resident owner actions', () => {
  it('reads without installing, connecting or selecting a host', async () => {
    const f = fixture(); await f.controller.refresh(); expect(f.last().value?.state).toBe('running');
    for (const method of ['install', 'start', 'stop', 'update', 'applyModel', 'connect'] as const) expect(f.client[method]).not.toHaveBeenCalled();
    expect(f.onUse).not.toHaveBeenCalled(); expect(f.onStatus).toHaveBeenCalledWith(expect.objectContaining({ revision: 'rev-one' }));
  });
  it('requires explicit model copying and keeps scheduled jobs off unless selected', async () => {
    const f = fixture(status({ installed: false, state: 'not_installed', launchId: undefined })); await f.controller.refresh();
    await f.controller.change('install', { copyModelProfile: false, cronEnabled: false });
    expect(f.client.install).not.toHaveBeenCalled(); expect(f.last().error).toContain('模型配置');
    await f.controller.refresh(); await f.controller.change('install', { copyModelProfile: true, cronEnabled: false });
    expect(f.client.install).toHaveBeenCalledWith({ expectedRevision: 'rev-one', copyModelProfile: true, cronEnabled: false });
    expect(f.client.connect).not.toHaveBeenCalled(); expect(f.onUse).not.toHaveBeenCalled();
  });
  it.each(['revision', 'launchId'] as const)('a changed %s blocks writes after fresh read', async key => {
    const f = fixture(); await f.controller.refresh(); f.value.current = { ...f.value.current, [key]: 'changed' };
    await f.controller.change('stop'); expect(f.client.stop).not.toHaveBeenCalled(); expect(f.last().error).toContain('变化');
  });
  it('sends launch identity for running actions and keeps update separate from model copy', async () => {
    const f = fixture(); await f.controller.refresh(); await f.controller.change('update');
    expect(f.client.update).toHaveBeenCalledWith({ expectedRevision: 'rev-one', expectedLaunchId: 'launch-one' });
    expect(f.client.applyModel).not.toHaveBeenCalled(); expect(f.client.connect).not.toHaveBeenCalled();
    expect(f.last().notice).toContain('程序');
  });
  it('allows program update on a stopped instance without a launch identity', async () => {
    const f = fixture(status({ state: 'stopped', launchId: undefined })); await f.controller.refresh(); await f.controller.change('update');
    expect(f.client.update).toHaveBeenCalledWith({ expectedRevision: 'rev-one' });
  });
  it('does not connect a stopped service or manufacture a selected host', async () => {
    const f = fixture(status({ state: 'stopped', launchId: undefined })); await f.controller.refresh(); await f.controller.change('connect');
    expect(f.client.connect).not.toHaveBeenCalled(); expect(f.onUse).not.toHaveBeenCalled();
  });
  it('does not connect a running service whose launch identity is unknown', async () => {
    const f = fixture(status({ launchId: undefined })); await f.controller.refresh(); await f.controller.change('connect');
    expect(f.client.connect).not.toHaveBeenCalled(); expect(f.onUse).not.toHaveBeenCalled(); expect(f.last().error).toContain('运行实例');
  });
  it('selects only after an explicit connect and waits for the existing host connection path', async () => {
    const f = fixture(); await f.controller.refresh(); await f.controller.change('connect');
    expect(f.client.connect).toHaveBeenCalledWith({ expectedRevision: 'rev-one' }); expect(f.onUse).toHaveBeenCalledWith(profile);
  });
  it.each(['stopped', 'changed-revision', 'changed-launch'] as const)('does not select after connect when the service becomes %s', async change => {
    const f = fixture(); await f.controller.refresh();
    vi.mocked(f.client.connect).mockImplementationOnce(async () => {
      f.value.current = status(change === 'stopped' ? { state: 'stopped' } : change === 'changed-revision' ? { revision: 'rev-new' } : { launchId: 'launch-new' });
      return profile;
    });
    await f.controller.change('connect');
    expect(f.onUse).not.toHaveBeenCalled(); expect(f.last().error).toContain('变化');
  });
  it('a late status read cannot replace a newer response', async () => {
    const f = fixture(); let finish!: (value: LocalResidentStatus) => void;
    vi.mocked(f.client.read).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const old = f.controller.refresh(); await f.controller.refresh();
    finish(status({ revision: 'rev-obsolete', state: 'stopped' })); await old;
    expect(f.last().value?.revision).toBe('rev-one'); expect(f.last().busy).toBe(false);
  });
  it('shows safe native string failures without reporting a successful mutation', async () => {
    const f = fixture(); await f.controller.refresh(); vi.mocked(f.client.stop).mockRejectedValueOnce('仍有活动，请核对');
    await f.controller.change('stop');
    expect(f.last().error).toContain('仍有活动'); expect(f.last().notice).toBe(''); expect(f.client.stop).toHaveBeenCalledTimes(1);
  });
  it('failures stay visible and never retry a mutation automatically', async () => {
    const f = fixture(); await f.controller.refresh(); vi.mocked(f.client.stop).mockRejectedValueOnce(new Error('仍有活动，请核对'));
    await f.controller.change('stop'); expect(f.last().error).toContain('仍有活动'); expect(f.last().notice).toBe('');
    expect(f.client.stop).toHaveBeenCalledTimes(1); expect(f.onUse).not.toHaveBeenCalled();
  });
  it('fences concurrent mutations and disposal during the fresh read', async () => {
    const f = fixture(); await f.controller.refresh(); let finish!: (s: LocalResidentStatus) => void;
    vi.mocked(f.client.read).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const pending = f.controller.change('stop'); await f.controller.change('update'); f.controller.dispose(); const count = f.states.length;
    finish(status()); await pending; expect(f.client.stop).not.toHaveBeenCalled(); expect(f.client.update).not.toHaveBeenCalled(); expect(f.states).toHaveLength(count);
  });
  it('a disposed connect cannot switch the app and a failed app connection is not success', async () => {
    const f = fixture(); await f.controller.refresh(); let finish!: (p: DesktopProjectProfile) => void;
    vi.mocked(f.client.connect).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const pending = f.controller.change('connect'); await vi.waitFor(() => expect(f.client.connect).toHaveBeenCalled()); f.controller.dispose(); finish(profile); await pending;
    expect(f.onUse).not.toHaveBeenCalled();
    const g = fixture(); await g.controller.refresh(); g.onUse.mockRejectedValueOnce(new Error('连接失败'));
    await g.controller.change('connect'); expect(g.last().error).toContain('连接失败'); expect(g.last().notice).toBe('');
  });
});
