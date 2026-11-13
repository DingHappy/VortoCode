export interface RecoveryItem {
  id: string; digest: string; prompt: string; member: string; team: string | null; round: number;
  error: string; result: string; log: string[]; messages: Array<{ kind: string; body: string }>;
  usage: unknown; approval: unknown; execution?: Record<string, unknown>; effects: string;
}
export interface RecoverySnapshot { instance: string; items: RecoveryItem[] }
export type RecoverySelection = Pick<RecoveryItem, 'id' | 'digest'>;
export type RecoveryAction = 'rework' | 'abandon';
export interface RecoveryClient {
  readStaffRecovery(): Promise<RecoverySnapshot>;
  decideStaffRecovery(selected: RecoverySelection[], action: RecoveryAction, instance: string): Promise<{
    results: Array<{ id: string; status: string; error?: string }>;
  }>;
}
export interface RecoveryState { value: RecoverySnapshot | null; busy: boolean; error: string; notice?: string }

export function recoveryController(client: RecoveryClient, publish: (value: RecoveryState) => void) {
  let active = true;
  let state: RecoveryState = { value: null, busy: false, error: '' };
  const emit = (patch: Partial<RecoveryState>) => { if (active) { state = { ...state, ...patch }; publish(state); } };
  async function refresh() {
    if (!active || state.busy) return;
    emit({ busy: true, error: '' });
    try { const value = await client.readStaffRecovery(); emit({ value }); }
    catch (error) { emit({ error: error instanceof Error ? error.message : '中断任务读取失败' }); }
    finally { emit({ busy: false }); }
  }
  async function decide(selected: RecoverySelection[], action: RecoveryAction, instance: string) {
    if (!active || state.busy || !state.value || state.error || !selected.length) return;
    emit({ busy: true, error: '', notice: '' });
    try {
      const fresh = await client.readStaffRecovery();
      if (!active) return;
      if (fresh.instance !== instance || selected.some(row => !fresh.items.some(item => item.id === row.id && item.digest === row.digest)))
        throw new Error('所选任务或运行实例已变化，请刷新后重新核对');
      const result = await client.decideStaffRecovery(selected, action, instance);
      if (!active) return;
      const failed = result.results.filter(item => item.status !== 'applied');
      const value = await client.readStaffRecovery();
      emit({ value, notice: failed.length ? `有 ${failed.length} 项未完成：${failed.map(item => `${item.id}：${item.error ?? '结果待核对'}`).join('；')}` : '已处理所选任务。返工不代表验收，也不会恢复旧审批。' });
    } catch (error) { emit({ error: error instanceof Error ? error.message : '结果待核对，请刷新' }); }
    finally { emit({ busy: false }); }
  }
  return { refresh, decide, dispose() { active = false; } };
}
