import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaffView, StaffPane } from "./StaffPane";
import { DevelopmentStaff } from './DevelopmentStaff';
import { StaffBudgets } from './StaffBudgets';
import type { StaffSnapshot } from "../../lib/staff";
import { SOURCE_FRESH_MS } from '../../lib/readFreshness';
import executionWire from '../../../../tests/fixtures/execution_observation_v1.json';
const noop = () => {};
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(1_000); });
afterEach(() => vi.useRealTimers());
const role = { id: "calendar", title: "日历只读", brief: "只查看日程", builtin: false, enabled: true, revision: 1, grants: { operations: ["read"], tools: ["calendar_events"], sees: ["plugin:calendar"], web: false, project: false }, engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 };
const snapshot: StaffSnapshot = { roles: [role], members: [{ id: "staff-calendar", name: "日程专员", role: "calendar", engine: "vortocode", model: "", status: "active", revision: 1, busy: 1, recent_tasks: [{ id: "task-fixture", prompt: "检查明天的安排", status: "done", review: "pending", delivery: "review" }] }], proposals: [{ id: "pending", digest: "digest", nonce: "nonce", approval_level: "desktop", round: 1, instance: "fixture", status: "pending", reason: "日历查询", snapshot: { kind: "role", data: role, previous: null } }] };
const displayed = (value = snapshot, error = '') => ({ value, busy: false, error, checkedAt: 1_000 });
it("shows grants, sources, web, recent work and owner controls", () => {
  const markup = renderToStaticMarkup(<StaffView state={displayed()} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  for (const text of ["日程专员", "calendar_events", "plugin:calendar", "不联网", "检查明天的安排", "暂停接活", "撤掉专员", "批准登记"]) expect(markup).toContain(text);
  expect(markup).toContain("尚未登记或授予权限"); expect(markup).not.toContain("主人已验收");
  expect(markup.match(/<button[^>]*>批准登记<\/button>/)?.[0]).not.toContain("disabled");
  expect(markup).not.toContain("一次性授权码"); expect(markup).not.toContain("Touch ID");
});
it("keeps unknown reads explicit and blocks changes until refresh", () => {
  const markup = renderToStaticMarkup(<StaffView state={displayed(snapshot, '读取失败')} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(markup).toContain('role="alert"'); expect(markup.match(/<button[^>]*>暂停接活<\/button>/)?.[0]).toContain("disabled"); expect(markup.match(/<button[^>]*>刷新名录<\/button>/)?.[0]).not.toContain("disabled");
});
it("renders proposal content as text without executing HTML", () => {
  const value = structuredClone(snapshot); value.proposals[0].reason = '<script>alert("fixture")</script>';
  const markup = renderToStaticMarkup(<StaffView state={displayed(value)} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(markup).toContain("&lt;script&gt;"); expect(markup).not.toContain("<script>");
});
it("does not treat the local project as the resident registry", () => { expect(renderToStaticMarkup(<StaffPane />)).toContain("登记常驻电脑"); });
it('keeps approval statistics and interrupted-task recovery available on the same connection', () => {
  const client = {
    readStaff: async () => snapshot,
    decideStaff: async () => ({}),
    setStaffStatus: async () => ({}),
    readApprovalStats: async () => ({}),
    readStaffRecovery: async () => ({ instance: 'fixture', items: [] }),
    decideStaffRecovery: async () => ({ results: [] }),
  };
  const html = renderToStaticMarkup(<StaffPane client={client} />);
  expect(html).toContain('审批统计（只读）');
  expect(html).toContain('中断任务核对');
});

it('shows task, execution and owner acceptance separately with safe report text', () => {
  const value = structuredClone(snapshot);
  value.members[0].recent_tasks[0].execution = { round: 1, stale: false, snapshot: {
    ...executionWire, state: 'finished', ref: { ...executionWire.ref, backend_id: 'vendor.future', task_id: 'task-fixture' },
    progress: { summary: '<script>untrusted progress</script>' },
  } };
  const html = renderToStaticMarkup(<StaffView state={displayed(value)} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  for (const text of ['已交回', '待核对', '执行观察：本次执行已结束', 'vendor.future', '第 1 轮', '不代表任务验收通过']) expect(html).toContain(text);
  expect(html).not.toContain('主人已验收'); expect(html).not.toContain('<script>');
  expect(html).toContain('&lt;script&gt;untrusted progress');
});

it.each(['missing', 'wrong task', 'stale', 'disconnected'])('keeps unavailable execution evidence explicit: %s', fault => {
  const value = structuredClone(snapshot), task = value.members[0].recent_tasks[0];
  if (fault !== 'missing') task.execution = { round: 1, stale: fault === 'stale', snapshot: {
    ...executionWire, state: 'running', ref: { ...executionWire.ref, task_id: fault === 'wrong task' ? 'other' : task.id },
  } };
  const html = renderToStaticMarkup(<StaffView state={displayed(value, fault === 'disconnected' ? '离线' : '')} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(html).toContain('执行观察：无法确认');
  expect(html).not.toContain('执行观察：在忙'); expect(html).not.toContain('主人已验收');
});
it('shows standing suggestions as unapproved guidance without adding activation controls', () => {
  const value = structuredClone(snapshot);
  value.standing_specialists = [{ id: 'calendar', title: '日程专员建议', scope: '只读日历', guide: '说明每天的时间和时区' },
    { id: 'mail', title: '邮件专员建议', scope: '逐次审批后存草稿，不发送', guide: '说明两个汇报时间' },
    { id: 'research', title: '资料专员建议', scope: '只查公开资料', guide: '说明公开研究话题' }];
  const html = renderToStaticMarkup(<StaffView state={displayed(value)} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(html).toContain('登记建议，尚未开通'); expect(html).toContain('再核对整张开通单后批准');
  expect(html).toContain('邮件专员建议'); expect(html).toContain('资料专员建议');
  expect(html).not.toContain('一键开通'); expect(html).toContain('本步不改为实时提醒');
});
it('escapes text in standing suggestions and preserves the guidance when disconnected', () => {
  const value = structuredClone(snapshot);
  value.standing_specialists = [{ id: 'fixture', title: '<script>fixture</script>', scope: '合成', guide: '待核对' }];
  const html = renderToStaticMarkup(<StaffView state={displayed(value, '离线')} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(html).not.toContain('<script>'); expect(html).toContain('&lt;script&gt;'); expect(html).toContain('上次读取的数据');
});

it('shows fixed project boundaries and separates proposing from owner confirmation', () => {
  const value = structuredClone(snapshot);
  value.development_projects = [{ name: 'Example' }];
  value.roles.push({ ...role, id: 'developer-example', title: '项目开发', project: 'Example',
    project_binding: { name: 'Example', repo_root: '/fixture/project', port: 8099 }, grants: { ...role.grants, project: true } });
  const html = renderToStaticMarkup(<DevelopmentStaff value={value} disabled={false} onPropose={noop} />);
  expect(html).toContain('固定项目'); expect(html).toContain('选择已登记项目');
  expect(html).toContain('提交岗位提议'); expect(html).toContain('提交专员提议');
  expect(html).toContain('每一步都由主人核对后确认'); expect(html).toContain('换项目须重新批准');
  expect(html).not.toContain('批准钥匙'); expect(html).not.toContain('type="file"');
});

it('renders every bundle grant and routine timezone before the single owner approval', () => {
  const value = structuredClone(snapshot), proposal = value.proposals[0];
  proposal.snapshot = { kind: 'bundle', previous: null, data: {}, entries: [
    { kind: 'role', previous: null, data: role },
    { kind: 'member', previous: null, data: { id: 'staff-new', name: '新专员', role: role.id, engine: 'vortocode' }, role_snapshot: role },
    { kind: 'routine', previous: null, data: { id: 'routine-new', name: '每日安排', member: 'staff-new', schedule: 'at 09:00', timezone: 'Asia/Shanghai', prompt: '<script>仅作资料</script>', delivery: 'notify', enabled: true }, role_snapshot: role },
  ] };
  const html = renderToStaticMarkup(<StaffView state={displayed(value)} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  for (const text of ['专员开通单', 'calendar_events', 'plugin:calendar', '不联网', '常设放行：无', 'Asia/Shanghai', 'at 09:00', '批准整张开通单']) expect(html).toContain(text);
  expect(html).not.toContain('<script>'); expect(html).toContain('&lt;script&gt;');
  expect(html.match(/>批准整张开通单</g)?.length).toBe(1);
});

it('offers completion or cancellation of an interrupted bundle, and only cancellation once it has begun', () => {
  const value = structuredClone(snapshot), proposal = value.proposals[0];
  proposal.snapshot = { kind: 'bundle', previous: null, data: {}, entries: [] }; proposal.status = 'applying';
  const render = () => renderToStaticMarkup(<StaffView state={displayed(value)} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(render()).toContain('继续完成开通单'); expect(render()).toContain('取消开通单');
  proposal.status = 'cancelling';
  expect(render()).toContain('继续取消开通单'); expect(render().match(/<button[^>]*>批准整张开通单<\/button>/)?.[0]).toContain('disabled');
});

it.each(['expired', 'missing timestamp', 'invalid timestamp', 'clock rollback'])('does not present an old registry as current: %s', fault => {
  const value = structuredClone(snapshot), task = value.members[0].recent_tasks[0];
  task.status = 'running';
  task.execution = { round: 1, stale: false, snapshot: { ...executionWire, state: 'running',
    ref: { ...executionWire.ref, task_id: task.id } } };
  const checkedAt = fault === 'missing timestamp' ? undefined : fault === 'invalid timestamp' ? NaN : fault === 'clock rollback' ? 1_001 : 1_000;
  if (fault === 'expired') vi.advanceTimersByTime(SOURCE_FRESH_MS + 1);
  const html = renderToStaticMarkup(<StaffView state={{ ...displayed(value), checkedAt }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(html).toContain('上次读取的数据');
  expect(html).toContain('执行观察：无法确认');
  expect(html).not.toContain('执行观察：在忙');
  expect(html).toContain('上次：已启用');
  expect(html).toContain('上次：在忙');
  expect(html).toContain(task.prompt);
  expect(html.match(/<button[^>]*>批准登记<\/button>/)?.[0]).toContain('disabled');
  expect(html.match(/<button[^>]*>刷新名录<\/button>/)?.[0]).not.toContain('disabled');
});

const renderStaff = (value = snapshot) => renderToStaticMarkup(<StaffView state={displayed(value)} onRefresh={noop} onStatus={noop} onDecide={noop} onBudgets={noop} />);
const memberCard = (html: string) => {
  const card = html.match(/<article\b[^>]*aria-label="日程专员"[^>]*>([\s\S]*?)<\/article>/)?.[1];
  expect(card).toBeDefined();
  return card!;
};

it('puts the registered roster before optional role and budget settings, with both closed initially', () => {
  const value = structuredClone(snapshot);
  value.budget_settings = { revision: 1, daily_tokens: 20_000, task_tokens: 5_000 };
  value.usage = { total: 200 };
  const html = renderStaff(value), roster = html.indexOf('aria-label="已登记专员"');
  expect(roster).toBeGreaterThan(-1);
  for (const label of ['岗位登记', '使用额度']) {
    const disclosure = html.match(new RegExp(`<details\\b[^>]*aria-label="${label}"[^>]*>`))?.[0];
    expect(disclosure).toBeDefined();
    expect(disclosure).not.toMatch(/\bopen(?:=|\s|>)/);
    expect(html.indexOf(`aria-label="${label}"`)).toBeGreaterThan(roster);
  }
});

it('keeps member grants, task evidence and status controls inside the closed member details', () => {
  const card = memberCard(renderStaff());
  const disclosure = card.match(/<details\b[^>]*class="staff-member-details"[^>]*>/)?.[0];
  expect(disclosure).toBeDefined();
  expect(disclosure).not.toMatch(/\bopen(?:=|\s|>)/);
  expect(card).toContain('<summary>查看详情</summary>');
  const detailsAt = card.indexOf(disclosure!);
  expect(card.slice(0, detailsAt)).toContain('已启用');
  expect(card.slice(0, detailsAt)).not.toContain('可接活');
  for (const text of ['calendar_events', '检查明天的安排', '暂停接活', '撤掉专员']) {
    expect(card.indexOf(text)).toBeGreaterThan(detailsAt);
  }
});

it.each(['missing role', 'disabled role', 'wrong engine', 'unbound project'])('keeps a dispatch blocker visible outside member details: %s', fault => {
  const value = structuredClone(snapshot);
  if (fault === 'missing role') value.roles = [];
  if (fault === 'disabled role') value.roles[0].enabled = false;
  if (fault === 'wrong engine') value.members[0].engine = 'opencode';
  if (fault === 'unbound project') value.roles[0].grants.project = true;
  const card = memberCard(renderStaff(value));
  const firstDetails = card.indexOf('<details');
  const visible = firstDetails < 0 ? card : card.slice(0, firstDetails);
  expect(visible).toContain('role="alert"');
  expect(visible).toMatch(/岗位|执行|项目/);
  expect(visible).not.toContain('可接活');
});

it('keeps proposal grants visible before approval rather than hiding them with member details', () => {
  const html = renderStaff();
  const proposal = html.match(/<section\b[^>]*aria-label="待批登记"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  expect(proposal).toBeDefined();
  const fullGrants = proposal!.indexOf('class="staff-grants"');
  expect(fullGrants).toBeGreaterThan(-1);
  expect(proposal!.indexOf('calendar_events')).toBeGreaterThan(fullGrants);
  expect(proposal!.indexOf('plugin:calendar')).toBeGreaterThan(fullGrants);
  expect(proposal!.indexOf('<details')).toBeGreaterThan(fullGrants);
  expect(proposal!.indexOf('批准登记')).toBeGreaterThan(fullGrants);
});

it('does not claim previous records exist when the first registry read fails', () => {
  const html = renderToStaticMarkup(<StaffView state={{ value: null, busy: false, error: '合成首次读取失败' }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(html).toContain('role="alert"');
  expect(html).toContain('合成首次读取失败');
  expect(html).not.toContain('上次读取的数据');
  expect(html).not.toContain('已保留上次');
});

it.each([undefined, { error: '合成用量来源不可读取' }])('does not turn unavailable usage into zero: %j', usage => {
  const html = renderToStaticMarkup(<StaffBudgets value={{ revision: 1, daily_tokens: 20_000, task_tokens: 5_000 }} usage={usage} disabled={false} onSave={noop} />);
  expect(html).not.toContain('今天已记录 0 token');
  if (usage?.error) expect(html).toContain(usage.error);
  else expect(html).toMatch(/待核对|无法核对|尚未核对/);
});

it('keeps usage errors visible before the closed budget disclosure', () => {
  const value = structuredClone(snapshot);
  value.budget_settings = { revision: 1, daily_tokens: 20_000, task_tokens: 5_000 };
  value.usage = { error: '合成用量来源不可读取' };
  const html = renderStaff(value);
  const errorAt = html.indexOf(value.usage.error!);
  const disclosure = html.match(/<details\b[^>]*aria-label="使用额度"[^>]*>/)?.[0];
  expect(disclosure).toBeDefined();
  expect(errorAt).toBeGreaterThan(-1);
  expect(errorAt).toBeLessThan(html.indexOf(disclosure!));
});
