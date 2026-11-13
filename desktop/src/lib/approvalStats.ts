const outcomes = ['approved', 'denied', 'expired', 'pending', 'cancelled', 'interrupted', 'unknown'] as const;
const bases = ['existing_trust', 'turn', 'task', 'standing', 'task_workspace', 'protective', 'unknown'] as const;
const sources = ['main', 'staff', 'routine', 'registration', 'other'] as const;
const kinds = ['read', 'write', 'execute', 'deliver', 'interact', 'own_data', 'draft', 'irreversible', 'internal', 'unknown'];
type Counts = Record<typeof outcomes[number] | 'count' | 'timed', number> & { median_wait_seconds: number | null };
export type ApprovalGroup = Counts & { day: string; source: typeof sources[number]; source_id: string; kind: string; tainted: boolean | 'unknown' };
export interface ApprovalStats {
  days: number; timezone: 'UTC'; from: string; to: string; groups: ApprovalGroup[]; totals: Counts;
  automatic: Record<typeof bases[number], number>; system_denied: number;
  coverage: { audit: string; tasks: string; missing_wait: number; unknown_metadata: number; invalid_timestamps: number };
}
export interface ApprovalStatsClient { readApprovalStats(days: number): Promise<unknown> }
export interface ApprovalStatsState { value: ApprovalStats | null; busy: boolean; error: string }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('统计格式无效');
  return value as Record<string, unknown>;
}
function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('统计计数无效');
  return value;
}
function counts(value: unknown): Counts {
  const data = object(value), result = Object.fromEntries([...outcomes, 'count', 'timed'].map(key => [key, count(data[key])])) as Counts;
  const wait = data.median_wait_seconds;
  if (wait !== null && (typeof wait !== 'number' || !Number.isFinite(wait) || wait < 0)) throw new Error('统计时长无效');
  if (outcomes.reduce((n, key) => n + result[key], 0) !== result.count || result.timed > result.count) throw new Error('统计合计不一致');
  result.median_wait_seconds = wait as number | null;
  return result;
}
export function parseApprovalStats(value: unknown): ApprovalStats {
  const data = object(value), days = count(data.days), coverage = object(data.coverage), automatic = object(data.automatic);
  if (days < 1 || days > 31 || data.timezone !== 'UTC' || !Array.isArray(data.groups) || data.groups.length > 20000) throw new Error('统计范围无效');
  if (![data.from, data.to].every(x => typeof x === 'string' && Number.isFinite(Date.parse(x)))) throw new Error('统计日期无效');
  if (![coverage.audit, coverage.tasks].every(x => ['current', 'partial', 'unavailable'].includes(String(x)))) throw new Error('统计来源未知');
  const groups = data.groups.map(value => {
    const row = object(value);
    if (typeof row.day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(row.day)
      || !sources.includes(row.source as typeof sources[number]) || !kinds.includes(String(row.kind))
      || typeof row.source_id !== 'string' || !/^[A-Za-z0-9_-]{0,160}$/.test(row.source_id)
      || (typeof row.tainted !== 'boolean' && row.tainted !== 'unknown')) throw new Error('统计分组无效');
    return { ...counts(row), day: row.day, source: row.source as typeof sources[number], source_id: row.source_id,
      kind: String(row.kind), tainted: row.tainted } as ApprovalGroup;
  });
  const totals = counts(data.totals);
  if (groups.reduce((n, row) => n + row.count, 0) !== totals.count) throw new Error('统计合计不一致');
  return { days, timezone: 'UTC', from: String(data.from), to: String(data.to), groups, totals,
    automatic: Object.fromEntries(bases.map(key => [key, count(automatic[key] ?? 0)])) as ApprovalStats['automatic'],
    system_denied: count(data.system_denied), coverage: { audit: String(coverage.audit), tasks: String(coverage.tasks),
      missing_wait: count(coverage.missing_wait), unknown_metadata: count(coverage.unknown_metadata), invalid_timestamps: count(coverage.invalid_timestamps) } };
}
export function approvalStatsController(client: ApprovalStatsClient, publish: (state: ApprovalStatsState) => void) {
  let active = true, serial = 0, window = 7;
  let state: ApprovalStatsState = { value: null, busy: false, error: '' };
  const emit = (patch: Partial<ApprovalStatsState>) => { if (active) { state = { ...state, ...patch }; publish(state); } };
  return {
    async refresh(days = window) {
      if (!active || state.busy) return;
      const request = ++serial;
      emit({ busy: true, error: '' });
      try {
        const value = parseApprovalStats(await client.readApprovalStats(days));
        if (active && request === serial) { window = days; emit({ value }); }
      } catch { if (active && request === serial) emit({ error: '统计读取失败，请刷新核对' }); }
      finally { if (active && request === serial) emit({ busy: false }); }
    },
    dispose() { active = false; ++serial; },
  };
}
