import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaffView, StaffPane } from "./StaffPane";
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
it('shows standing suggestions as unapproved guidance without adding activation controls', () => {
  const value = structuredClone(snapshot);
  value.standing_specialists = [{ id: 'calendar', title: '日程专员建议', scope: '只读日历', guide: '说明每天的时间和时区' },
    { id: 'mail', title: '邮件专员建议', scope: '逐次审批后存草稿，不发送', guide: '说明两个汇报时间' },
    { id: 'research', title: '资料专员建议', scope: '只查公开资料', guide: '说明公开研究话题' }];
  const html = renderToStaticMarkup(<StaffView state={{ value, busy: false, error: '' }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(html).toContain('登记建议，尚未开通'); expect(html).toContain('再逐项核对并批准');
  expect(html).toContain('邮件专员建议'); expect(html).toContain('资料专员建议');
  expect(html).not.toContain('一键开通'); expect(html).toContain('本步不改为实时提醒');
});
it('escapes text in standing suggestions and preserves the guidance when disconnected', () => {
  const value = structuredClone(snapshot);
  value.standing_specialists = [{ id: 'fixture', title: '<script>fixture</script>', scope: '合成', guide: '待核对' }];
  const html = renderToStaticMarkup(<StaffView state={{ value, busy: false, error: '离线' }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  expect(html).not.toContain('<script>'); expect(html).toContain('&lt;script&gt;'); expect(html).toContain('上次读取的数据');
});
