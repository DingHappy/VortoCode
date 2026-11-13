import type { CodexProgressSnapshot } from '../types';
import { readExecutionObservation, type ExecutionObservation } from './executionObservation';
import { freshAt } from './readFreshness';

// Matches the resident watch's STALE_AFTER. This is observation freshness, not approval TTL.
export const CODEX_REPORT_FRESH_MS = 30 * 60_000;
type Thread = CodexProgressSnapshot['threads'][number];

/** Keep native/old-wire interpretation at the source boundary, outside product views. */
export function codexExecutionObservation(thread: Thread, now: number): {
  state: ExecutionObservation['state']; active: boolean; summary: string;
} {
  const hasExecution = Object.prototype.hasOwnProperty.call(thread, 'execution');
  const execution = hasExecution ? readExecutionObservation(thread.execution) : null;
  const valid = execution !== null && execution.ref.backend_id === 'codex'
    && execution.ref.session_id === thread.id && execution.ref.turn_id === thread.turn?.id
    && execution.ref.run_id === thread.turn?.id && !execution.ref.task_id
    && freshAt(execution.observed_at * 1000, now, CODEX_REPORT_FRESH_MS);
  const active = thread.status === 'active' || (valid && ['running', 'waiting_user', 'queued'].includes(execution.state));
  if (hasExecution) return { state: valid ? execution.state : 'unknown', active, summary: valid ? execution.progress.summary : '' };

  // Missing field is an old observer. An explicit null/bad v1 above never falls back.
  let state: ExecutionObservation['state'] = 'unknown';
  if (['active', 'idle'].includes(thread.status)) {
    if (thread.flags.includes('waitingOnApproval') || thread.flags.includes('waitingOnUserInput')) state = 'waiting_user';
    else if (active) state = thread.turn?.status === 'inProgress' ? 'running' : 'unknown';
    else if (thread.turn?.status === 'failed') state = 'failed';
    else if (thread.turn?.status === 'interrupted') state = 'cancelled';
    else if (thread.turn?.status === 'completed') state = 'finished';
  }
  return { state, active, summary: '' };
}
