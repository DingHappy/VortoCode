import type { TeamOutput, TeamOutputPage } from '../types';

export interface TeamOutputClient {
  getTeamOutput(team: string, task: string, round: number, offset: number, sha256: string): Promise<TeamOutputPage>;
}
export async function appendOutputPage(client: TeamOutputClient, team: string, expected: TeamOutput,
  text: string, offset: number, isCurrent: () => boolean) {
  if (expected.version !== 1 || !/^[0-9a-f]{64}$/.test(expected.sha256) || !Number.isSafeInteger(expected.characters)
    || expected.characters < 0 || !Number.isSafeInteger(expected.round) || expected.round < 1)
    throw new Error('交付文件记录无效，请重新核对');
  const page = await client.getTeamOutput(team, expected.task_id, expected.round, offset, expected.sha256);
  if (!isCurrent()) return null;
  const ref = page.output, count = Array.from(page.text ?? '').length;
  if (!ref || ref.sha256 !== expected.sha256 || ref.task_id !== expected.task_id || ref.round !== expected.round
    || ref.characters !== expected.characters || page.offset !== offset || typeof page.text !== 'string'
    || count > 8000 || offset + count > expected.characters || Array.from(text).length !== offset
    || page.complete !== (page.next_offset === null) || page.next_offset !== null && page.next_offset !== offset + count
    || page.next_offset === null && offset + count !== expected.characters || page.next_offset !== null && count === 0)
    throw new Error('交付分页或版本不一致，未把它拼成完整正文');
  const combined = text + page.text;
  if (page.complete) {
    if (!crypto.subtle) throw new Error('当前环境不能核对文件摘要，未标为完整读取');
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(combined));
    const hash = Array.from(new Uint8Array(bytes), n => n.toString(16).padStart(2, '0')).join('');
    if (!isCurrent()) return null;
    if (hash !== expected.sha256) throw new Error('完整正文摘要不一致，未标为读完');
  }
  return { text: combined, next: page.next_offset, complete: page.complete };
}
