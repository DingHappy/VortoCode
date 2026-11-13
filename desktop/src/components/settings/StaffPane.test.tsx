import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaffView, StaffPane } from "./StaffPane";
import { DevelopmentStaff } from './DevelopmentStaff';
import type { StaffSnapshot } from "../../lib/staff";
const noop = () => {};
const role = { id: "calendar", title: "日历只读", brief: "只查看日程", builtin: false, enabled: true, revision: 1, grants: { operations: ["read"], tools: ["calendar_events"], sees: ["plugin:calendar"], web: false, project: false }, engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 };
const snapshot: StaffSnapshot = { roles: [role], members: [{ id: "staff-calendar", name: "日程专员", role: "calendar", engine: "vortocode", model: "", status: "active", revision: 1, busy: 1, recent_tasks: [{ id: "task-fixture", prompt: "检查明天的安排", status: "done", review: "pending", delivery: "review" }] }], proposals: [{ id: "pending", digest: "digest", nonce: "nonce", approval_level: "desktop", round: 1, instance: "fixture", status: "pending", reason: "日历查询", snapshot: { kind: "role", data: role, previous: null } }] };
it("shows grants, sources, web, recent work and owner controls", () => {
  const markup = renderToStaticMarkup(<StaffView state={{ value: snapshot, busy: false, error: "" }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  for (const text of ["日程专员", "calendar_events", "plugin:calendar", "不联网", "检查明天的安排", "暂停接活", "撤掉专员", "批准登记"]) expect(markup).toContain(text);
  expect(markup).toContain("尚未登记或授予权限"); expect(markup).not.toContain("主人已验收");
  expect(markup.match(/<button[^>]*>批准登记<\/button>/)?.[0]).not.toContain("disabled");
  expect(markup).not.toContain("一次性授权码"); expect(markup).not.toContain("Touch ID");
});
it("keeps unknown reads explicit and blocks changes until refresh", () => {
  const markup = renderToStaticMarkup(<StaffView state={{ value: snapshot, busy: false, error: "读取失败" }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(markup).toContain('role="alert"'); expect(markup.match(/<button[^>]*>暂停接活<\/button>/)?.[0]).toContain("disabled"); expect(markup.match(/<button[^>]*>刷新名录<\/button>/)?.[0]).not.toContain("disabled");
});
it("renders proposal content as text without executing HTML", () => {
  const value = structuredClone(snapshot); value.proposals[0].reason = '<script>alert("fixture")</script>';
  const markup = renderToStaticMarkup(<StaffView state={{ value, busy: false, error: "" }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
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
it('shows standing suggestions as unapproved guidance without adding activation controls', () => {
  const value = structuredClone(snapshot);
  value.standing_specialists = [{ id: 'calendar', title: '日程专员建议', scope: '只读日历', guide: '说明每天的时间和时区' },
    { id: 'mail', title: '邮件专员建议', scope: '逐次审批后存草稿，不发送', guide: '说明两个汇报时间' },
    { id: 'research', title: '资料专员建议', scope: '只查公开资料', guide: '说明公开研究话题' }];
  const html = renderToStaticMarkup(<StaffView state={{ value, busy: false, error: '' }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(html).toContain('登记建议，尚未开通'); expect(html).toContain('再核对整张开通单后批准');
  expect(html).toContain('邮件专员建议'); expect(html).toContain('资料专员建议');
  expect(html).not.toContain('一键开通'); expect(html).toContain('本步不改为实时提醒');
});
it('escapes text in standing suggestions and preserves the guidance when disconnected', () => {
  const value = structuredClone(snapshot);
  value.standing_specialists = [{ id: 'fixture', title: '<script>fixture</script>', scope: '合成', guide: '待核对' }];
  const html = renderToStaticMarkup(<StaffView state={{ value, busy: false, error: '离线' }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
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
  const html = renderToStaticMarkup(<StaffView state={{ value, busy: false, error: '' }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  for (const text of ['专员开通单', 'calendar_events', 'plugin:calendar', '不联网', '常设放行：无', 'Asia/Shanghai', 'at 09:00', '批准整张开通单']) expect(html).toContain(text);
  expect(html).not.toContain('<script>'); expect(html).toContain('&lt;script&gt;');
  expect(html.match(/>批准整张开通单</g)?.length).toBe(1);
});

it('offers completion or cancellation of an interrupted bundle, and only cancellation once it has begun', () => {
  const value = structuredClone(snapshot), proposal = value.proposals[0];
  proposal.snapshot = { kind: 'bundle', previous: null, data: {}, entries: [] }; proposal.status = 'applying';
  const render = () => renderToStaticMarkup(<StaffView state={{ value, busy: false, error: '' }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(render()).toContain('继续完成开通单'); expect(render()).toContain('取消开通单');
  proposal.status = 'cancelling';
  expect(render()).toContain('继续取消开通单'); expect(render().match(/<button[^>]*>批准整张开通单<\/button>/)?.[0]).toContain('disabled');
});
