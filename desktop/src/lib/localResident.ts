import { invoke } from '@tauri-apps/api/core';
import type { DesktopProjectProfile } from '../types';

export interface LocalResidentStatus {
  supported: boolean;
  installed: boolean;
  state: 'not_installed' | 'stopped' | 'starting' | 'running' | 'unavailable';
  version?: string;
  availableVersion: string;
  cronEnabled: boolean;
  projectId?: string;
  launchId?: string;
  revision?: string;
  modelRevision?: string;
  message: string;
}
export type LocalResidentAction = 'install' | 'start' | 'stop' | 'update' | 'apply_model' | 'connect';
export interface LocalResidentInstall { cronEnabled: boolean; copyModelProfile: boolean }
export interface LocalResidentExpected { expectedRevision: string; expectedLaunchId?: string }
export interface LocalResidentClient {
  read(): Promise<LocalResidentStatus>;
  install(options: LocalResidentInstall & LocalResidentExpected): Promise<LocalResidentStatus>;
  start(expected: LocalResidentExpected): Promise<LocalResidentStatus>;
  stop(expected: LocalResidentExpected): Promise<LocalResidentStatus>;
  update(expected: LocalResidentExpected): Promise<LocalResidentStatus>;
  applyModel(expected: LocalResidentExpected): Promise<LocalResidentStatus>;
  connect(expected: LocalResidentExpected): Promise<DesktopProjectProfile>;
}
export interface LocalResidentState { value: LocalResidentStatus | null; busy: boolean; error: string; notice: string; checkedAt?: number }
export const INITIAL_LOCAL_RESIDENT: LocalResidentState = { value: null, busy: false, error: '', notice: '' };

export const localResidentClient: LocalResidentClient = {
  read: () => invoke('local_resident_status'),
  install: options => invoke('local_resident_install', { ...options }),
  start: expected => invoke('local_resident_start', { ...expected }),
  stop: expected => invoke('local_resident_stop', { ...expected }),
  update: expected => invoke('local_resident_update', { ...expected }),
  applyModel: expected => invoke('local_resident_apply_model', { ...expected }),
  connect: expected => invoke('local_resident_connect', { ...expected }),
};

function checked(value: LocalResidentStatus): LocalResidentStatus {
  if (!value || typeof value.supported !== 'boolean' || typeof value.installed !== 'boolean'
    || !['not_installed', 'stopped', 'starting', 'running', 'unavailable'].includes(value.state)
    || typeof value.cronEnabled !== 'boolean' || typeof value.availableVersion !== 'string') {
    throw new Error('本机常驻状态无法核对，请刷新后重试');
  }
  return value;
}
function identity(value: LocalResidentStatus) {
  return JSON.stringify([value.supported, value.installed, value.state, value.revision, value.launchId,
    value.projectId, value.version, value.availableVersion, value.modelRevision, value.cronEnabled]);
}
function failure(error: unknown, fallback: string) { return error instanceof Error ? error.message : typeof error === 'string' && error.trim() ? error : fallback; }
export function localResidentController(client: LocalResidentClient, publish: (state: LocalResidentState) => void,
  onUse: (profile: DesktopProjectProfile) => Promise<void>, onStatus?: (status: LocalResidentStatus) => void) {
  let active = true, serial = 0, writing = false;
  let state: LocalResidentState = { ...INITIAL_LOCAL_RESIDENT };
  const emit = (patch: Partial<LocalResidentState>) => { if (active) { state = { ...state, ...patch }; publish(state); } };
  const useStatus = (value: LocalResidentStatus, checkedAt: number) => {
    if (!active) return;
    emit({ value: checked(value), checkedAt }); onStatus?.(value);
  };
  const read = async () => { const checkedAt = Date.now(); return { value: checked(await client.read()), checkedAt }; };
  async function refresh() {
    if (!active || writing) return;
    const request = ++serial; emit({ busy: true, error: '', notice: '' });
    try { const fresh = await read(); if (active && request === serial) useStatus(fresh.value, fresh.checkedAt); }
    catch (error) { if (active && request === serial) emit({ error: failure(error, '无法读取本机常驻状态') }); }
    finally { if (active && request === serial) emit({ busy: false }); }
  }
  async function change(action: LocalResidentAction, options?: LocalResidentInstall) {
    if (!active || state.busy || state.error || !state.value || writing) return;
    const displayed = state.value;
    writing = true; const request = ++serial; emit({ busy: true, error: '', notice: '' });
    const current = () => active && request === serial;
    try {
      const fresh = await read(); if (!current()) return;
      useStatus(fresh.value, fresh.checkedAt);
      if (identity(displayed) !== identity(fresh.value)) throw new Error('本机常驻状态已变化，请核对后再操作');
      const value = fresh.value;
      if (!value.supported || !value.revision || value.state === 'unavailable' || value.state === 'starting') {
        throw new Error('当前状态尚未就绪，请刷新核对后再操作');
      }
      const expected: LocalResidentExpected = { expectedRevision: value.revision };
      if (action === 'install') {
        if (value.installed || !options?.copyModelProfile) throw new Error('安装前请明确同意复制当前模型配置');
        const next = await client.install({ ...expected, cronEnabled: Boolean(options.cronEnabled), copyModelProfile: true });
        if (current()) { useStatus(next, Date.now()); emit({ notice: '安装操作已完成，尚未切换当前对话。' }); }
      } else {
        if (!value.installed) throw new Error('请先安装本机常驻助理');
        if (action === 'connect') {
          if (value.state !== 'running') throw new Error('本机常驻助理尚未运行，请先启动');
          if (!value.launchId) throw new Error('运行实例无法核对，请刷新后重试');
          const profile = await client.connect(expected); if (!current()) return;
          if (profile.kind !== 'remote' || profile.repoRoot !== '@assistant' || !profile.id
            || value.projectId && profile.id !== value.projectId) throw new Error('连接登记与本机常驻身份不一致，请刷新核对');
          const latest = await read(); if (!current()) return; useStatus(latest.value, latest.checkedAt);
          if (!latest.value.supported || latest.value.state !== 'running' || latest.value.revision !== value.revision
            || latest.value.launchId !== value.launchId || latest.value.projectId && latest.value.projectId !== profile.id) {
            throw new Error('本机常驻运行实例已变化，请刷新核对后再连接');
          }
          await onUse(profile); if (current()) emit({ notice: '已交给现有连接流程，请核对当前对话的位置。' });
        } else if (action === 'start') {
          if (value.state !== 'stopped') throw new Error('仅能启动已停止的本机常驻助理');
          const next = await client.start(expected); if (current()) { useStatus(next, Date.now()); emit({ notice: next.state === 'running' ? '本机常驻助理已启动，尚未切换当前对话。' : '启动操作已受理，请核对运行状态。' }); }
        } else {
          if (value.state === 'running') {
            if (!value.launchId) throw new Error('运行实例无法核对，请刷新后重试');
            expected.expectedLaunchId = value.launchId;
          }
          if (action === 'stop' && value.state !== 'running') throw new Error('本机常驻助理当前未运行');
          const next = await (action === 'stop' ? client.stop(expected) : action === 'update' ? client.update(expected) : client.applyModel(expected));
          if (current()) { useStatus(next, Date.now()); emit({ notice: action === 'stop' ? '停止操作已完成，请核对运行状态。' : action === 'update' ? '程序更新已完成，模型配置保持独立。' : '已应用当前保存的模型配置。' }); }
        }
      }
    } catch (error) { if (current()) emit({ error: failure(error, '操作结果无法确认，请刷新核对'), notice: '' }); }
    finally { writing = false; if (current()) emit({ busy: false }); }
  }
  return { refresh, change, dispose: () => { active = false; serial++; } };
}
