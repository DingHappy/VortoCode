import type { StaffRole } from './staff';

export interface RoutineDefinition {
  id: string; name: string; member: string; prompt: string; acceptance: string[];
  delivery: 'review' | 'notify'; schedule: string; timezone: string; enabled: boolean; revision: number; token_budget: number;
}
export interface StaffRoutine extends RoutineDefinition {
  retired: boolean; member_name: string; role_title: string; next: string | null;
  legacy: { name: string; announce: string; timeout: number; budget: number } | null;
  state: { cursor: string; task?: string | null; last_task?: string; last_status?: string; error?: string };
  last_delivery: { id: string; status: string; created: string; result: string; error: string } | null;
}
export interface RoutineMigration {
  config_revision: string | null; timezone: string; roles: StaffRole[];
  members: Array<{ id: string; name: string; role: string; model: string }>;
  routines: Array<{ data: RoutineDefinition; legacy: { announce: string; timeout: number; budget: number } }>;
  deferred: Array<{ name: string; reason: string }>;
}
export interface RoutinesSnapshot {
  revision: number; timezone: string; host_timezone: string | null; items: StaffRoutine[];
  scheduler_enabled: boolean; migrations: Array<{ id: string; status: string }>;
  migration: RoutineMigration | null;
}
export interface RoutinesClient {
  readRoutines(timezone?: string): Promise<RoutinesSnapshot>;
  changeRoutines(action: string, data: Record<string, unknown>): Promise<unknown>;
}
export interface RoutinesState { value: RoutinesSnapshot | null; busy: boolean; error: string; notice?: string }

export function scheduleProposal(item: StaffRoutine, schedule: string, timezone: string) {
  return { id: item.id, name: item.name, member: item.member, prompt: item.prompt, acceptance: item.acceptance,
    delivery: item.delivery, schedule, timezone, enabled: item.enabled, revision: item.revision, token_budget: item.token_budget };
}

export function routinesController(client: RoutinesClient, publish: (state: RoutinesState) => void, proposed: () => void = () => {}) {
  let active = true, serial = 0, migrationZone = '';
  let state: RoutinesState = { value: null, busy: false, error: '' };
  const emit = (patch: Partial<RoutinesState>) => { if (active) { state = { ...state, ...patch }; publish(state); } };
  async function refresh(zone = migrationZone) {
    if (!active || state.busy) return;
    const request = ++serial;
    emit({ busy: true, error: '' });
    try {
      const value = await client.readRoutines(zone);
      if (active && request === serial) { migrationZone = zone; emit({ value }); }
    } catch (reason) { if (active && request === serial) emit({ error: reason instanceof Error ? reason.message : '读取例行工作失败' }); }
    finally { if (active && request === serial) emit({ busy: false }); }
  }
  async function change(action: string, data: Record<string, unknown>) {
    if (!active || state.busy || !state.value || state.error) return;
    ++serial; emit({ busy: true, error: '', notice: '' });
    try {
      const fresh = await client.readRoutines(migrationZone);
      if (!active) return;
      if (action === 'timezone' && fresh.revision !== data.revision) throw new Error('默认时区已变化，请刷新');
      if (action === 'status' || action === 'delete') {
        if (!fresh.items.some(item => item.id === data.id && item.revision === data.revision && !item.retired))
          throw new Error('例行工作已变化，请刷新后重新核对');
      }
      if (action === 'migration') {
        const definition = data.definition as { config_revision?: string; timezone?: string };
        if (!fresh.migration || fresh.migration.config_revision !== definition.config_revision || fresh.migration.timezone !== definition.timezone)
          throw new Error('迁移清单或原时区已变化，请重新预览');
      }
      if (action === 'propose') {
        const definition = data.definition as { id: string; revision?: number };
        const current = fresh.items.find(item => item.id === definition.id);
        if (current && (current.retired || current.revision !== definition.revision))
          throw new Error('原例行工作已变化，请刷新后重新提议');
      }
      await client.changeRoutines(action, data);
      if (!active) return;
      const value = await client.readRoutines(migrationZone);
      if (active) {
        emit({ value, notice: action === 'migration' || action === 'propose' ? '提议已提交，尚未生效。请到上方待批登记核对后批准。' : '设置已保存，请核对当前状态。' });
        if (action === 'migration' || action === 'propose') proposed();
      }
    } catch (reason) { if (active) emit({ error: reason instanceof Error ? reason.message : '操作结果待核对，请刷新' }); }
    finally { if (active) emit({ busy: false }); }
  }
  return { refresh, change, dispose() { active = false; ++serial; } };
}
