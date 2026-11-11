import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaffView, StaffPane } from "./StaffPane";
import type { StaffSnapshot } from "../../lib/staff";
const noop = () => {};
const role = { id: "calendar", title: "日历只读", brief: "只查看日程", builtin: false, enabled: true, revision: 1, grants: { operations: ["read"], tools: ["calendar_events"], sees: ["plugin:calendar"], web: false, project: false }, engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 };
const snapshot: StaffSnapshot = { roles: [role], members: [{ id: "staff-calendar", name: "日程专员", role: "calendar", engine: "vortocode", model: "", status: "active", revision: 1, busy: 1, recent_tasks: [{ id: "task-fixture", prompt: "检查明天的安排", status: "done", review: "pending", delivery: "review" }] }], proposals: [{ id: "pending", digest: "digest", nonce: "nonce", requires_desktop_key: true, status: "pending", reason: "日历查询", snapshot: { kind: "role", data: role, previous: null } }], approval_key: { instance: "fixture", key: null } };
it("shows grants, sources, web, recent work and owner controls", () => {
  const markup = renderToStaticMarkup(<StaffView state={{ value: snapshot, busy: false, error: "" }} onRefresh={noop} onStatus={noop} onDecide={noop} />);
  for (const text of ["日程专员", "calendar_events", "plugin:calendar", "不联网", "检查明天的安排", "暂停接活", "撤掉专员", "先登记批准钥匙", "一次性授权码"]) expect(markup).toContain(text);
  expect(markup).toContain("尚未登记或授予权限"); expect(markup).not.toContain("主人已验收");
  expect(markup.match(/<button[^>]*>先登记批准钥匙<\/button>/)?.[0]).toContain("disabled");
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
