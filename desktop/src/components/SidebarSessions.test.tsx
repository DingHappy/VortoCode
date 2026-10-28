import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import type { DecisionItem, GoalItem, SessionSummary, TaskItem } from "../types";
import { SidebarFooter } from "./SidebarFooter";
import { SidebarSessions } from "./SidebarSessions";

const noop = () => {};
const handlers = { onSelect: noop, onFocusTask: noop, onOpenHooks: noop, onRename: noop, onDelete: noop,
  onOpenDecision: noop, onOpenGoals: noop, onOpenTasks: noop };
const session = (sid: string, title: string, extra: Partial<SessionSummary> = {}) =>
  ({ sid, title, updated: "2026-10-22T08:00:00Z", messages: 2, ...extra }) as SessionSummary;

it("lists sessions, filters by the search box and offers rename/delete", () => {
  const sessions = [session("a", "修登录超时"), session("b", "写周报")];
  const all = renderToStaticMarkup(<SidebarSessions sessions={sessions} activeSid="a" busy={false} query=""
    decisions={[]} goals={[]} tasks={[]} {...handlers} />);
  expect(all).toContain("修登录超时"); expect(all).toContain("写周报");
  expect(all).toContain("重命名任务"); expect(all).toContain("删除任务");
  expect(all).not.toContain("需要继续");
  const filtered = renderToStaticMarkup(<SidebarSessions sessions={sessions} activeSid="a" busy={false} query=" 周报 "
    decisions={[]} goals={[]} tasks={[]} {...handlers} />);
  expect(filtered).toContain("写周报"); expect(filtered).not.toContain("修登录超时");
  const empty = renderToStaticMarkup(<SidebarSessions sessions={[]} activeSid="" busy={false} query=""
    decisions={[]} goals={[]} tasks={[]} {...handlers} />);
  expect(empty).toContain("任务线程会出现在这里");
});

it("shows what needs the owner: background work, hooks and the work queue", () => {
  const sessions = [session("a", "修登录", { background_tasks: { active: 1, attention: 2 }, hook_issues: { count: 1 } } as Partial<SessionSummary>)];
  const decisions = [{ id: "d1", title: "确认写盘", detail: "src/a.py", severity: "high" }] as DecisionItem[];
  const goals = [{ id: "g1", objective: "上线登录", status: "active", progress: { passed: 1, total: 3 } }] as GoalItem[];
  const tasks = [{ id: "t1", status: "running", prompt: "跑迁移", branch: "vorto/migrate" }] as TaskItem[];
  const html = renderToStaticMarkup(<SidebarSessions sessions={sessions} activeSid="a" busy={false} query=""
    decisions={decisions} goals={goals} tasks={tasks} {...handlers} />);
  for (const text of ["1 后台", "2 待处理", "1 Hook", "需要继续", "确认写盘", "1/3 条完成标准", "跑迁移", "vorto/migrate"])
    expect(html).toContain(text);
});

it("footer shows the account, settings, and the update button only when there is one", () => {
  const identity = { initial: "D", name: "me@example.com", detail: "专业版会员", title: "VortoCode 账号" };
  const none = renderToStaticMarkup(<SidebarFooter identity={identity} update={null} onOpenAccount={noop} onOpenSettings={noop} />);
  expect(none).toContain("me@example.com"); expect(none).toContain("专业版会员"); expect(none).toContain('aria-label="设置"');
  expect(none).not.toContain("新版本");
  const update = { current: "0.2.14", latest: "0.2.15", newer: true, url: "https://github.com/DingHappy/VortoCode/releases/tag/v0.2.15", publishedAt: "" };
  const html = renderToStaticMarkup(<SidebarFooter identity={identity} update={update} onOpenAccount={noop} onOpenSettings={noop} />);
  expect(html).toContain("新版本 0.2.15"); expect(html).toContain("点击更新并重启");
});

it("footer shows where the assistant lives only when given", () => {
  const identity = { initial: "D", name: "me@example.com", detail: "", title: "VortoCode 账号" };
  const footer = (resident?: Parameters<typeof SidebarFooter>[0]["resident"]) =>
    renderToStaticMarkup(<SidebarFooter identity={identity} update={null} resident={resident} onOpenAccount={noop} onOpenSettings={noop} />);
  expect(footer()).not.toContain("vc-side-resident");
  expect(footer({ state: "online", name: "home", onClick: noop })).toContain("常驻电脑「home」 · 在线");
  expect(footer({ state: "offline", name: "常驻电脑", onClick: noop })).toContain("常驻电脑 · 连不上");
  expect(footer({ state: "local", onClick: noop })).toContain("在本机运行");
});
