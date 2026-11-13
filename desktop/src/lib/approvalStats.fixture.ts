export function sampleStats() {
  return { days: 7, timezone: 'UTC', from: '2026-11-06T00:00:00+00:00', to: '2026-11-12T12:00:00+00:00',
    groups: [{ day: '2026-11-12', source: 'main', source_id: '', kind: 'own_data', tainted: false,
      count: 2, approved: 1, denied: 1, expired: 0, pending: 0, cancelled: 0, interrupted: 0, unknown: 0, timed: 2, median_wait_seconds: 5 }],
    totals: { count: 2, approved: 1, denied: 1, expired: 0, pending: 0, cancelled: 0, interrupted: 0, unknown: 0, timed: 2, median_wait_seconds: 5 },
    automatic: { existing_trust: 3, turn: 1, task: 0, standing: 0, task_workspace: 0, protective: 0 },
    automatic_groups: [], system_denied: 1,
    coverage: { audit: 'current', tasks: 'current', missing_wait: 0, unknown_metadata: 0, invalid_timestamps: 0, scope_grants: 0, audit_decisions: 2, task_cards: 0 } };
}
