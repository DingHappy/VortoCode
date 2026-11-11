import { describe, expect, it } from 'vitest';
import { appendOutputPage } from './teamOutputs';
import type { TeamOutput, TeamOutputPage } from '../types';

const text = '证据🙂'.repeat(3000) + '最后一条';
const chars = Array.from(text);
const output: TeamOutput = { version: 1, task_id: 'task-0123456789', round: 1,
  sha256: 'd0917582f8648f8caf317c7f709d9f13b52a6044d709db656e0f8d0a878e8828',
  characters: chars.length, bytes: new TextEncoder().encode(text).length, tainted: true };
function page(offset: number): TeamOutputPage {
  const end = Math.min(chars.length, offset + 8000);
  return { output, text: chars.slice(offset, end).join(''), offset, next_offset: end < chars.length ? end : null,
    complete: end === chars.length, external_content: true };
}

it('reads every Unicode page and verifies the complete digest before claiming completion', async () => {
  const calls: number[] = [];
  const client = { async getTeamOutput(_team: string, _task: string, _round: number, offset: number) { calls.push(offset); return page(offset); } };
  const first = await appendOutputPage(client, 'team', output, '', 0, () => true);
  expect(first?.complete).toBe(false);
  const last = await appendOutputPage(client, 'team', output, first!.text, first!.next!, () => true);
  expect(last).toEqual({ text, next: null, complete: true }); expect(calls).toEqual([0, 8000]);
});

describe.each(['digest', 'task', 'round', 'offset', 'gap', 'partial-complete', 'text'] as const)('%s mismatch', mutation => {
  it('rejects the response instead of displaying an unverified full document', async () => {
    const bad = structuredClone(page(8000));
    if (mutation === 'digest') bad.output.sha256 = 'f'.repeat(64);
    if (mutation === 'task') bad.output.task_id = 'another';
    if (mutation === 'round') bad.output.round++;
    if (mutation === 'offset') bad.offset++;
    if (mutation === 'gap') { bad.next_offset = 9000; bad.complete = false; }
    if (mutation === 'partial-complete') bad.complete = false;
    if (mutation === 'text') bad.text = '替换' + bad.text.slice(2);
    await expect(appendOutputPage({ getTeamOutput: async () => bad }, 'team', output, chars.slice(0, 8000).join(''), 8000, () => true)).rejects.toThrow();
  });
});

it('discards a response after the source changes', async () => {
  expect(await appendOutputPage({ getTeamOutput: async () => page(0) }, 'team', output, '', 0, () => false)).toBeNull();
});

it('does not publish a complete document if source changes during digest verification', async () => {
  let checks = 0;
  expect(await appendOutputPage({ getTeamOutput: async () => page(8000) }, 'team', output,
    chars.slice(0, 8000).join(''), 8000, () => ++checks === 1)).toBeNull();
});
