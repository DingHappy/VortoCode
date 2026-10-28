import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { ConversationHeader } from "./ConversationHeader";

const header = (patch: Partial<Parameters<typeof ConversationHeader>[0]> = {}) => renderToStaticMarkup(
  <ConversationHeader title="修登录" place={null} panelOpen={false} waiting={0} plan={{ done: 0, total: 0 }} busy={false}
    onTogglePanel={() => {}} onStop={() => {}} {...patch} />);

it("says where the work runs only when a place is given", () => {
  expect(header()).not.toContain("conversation-place");
  expect(header({ place: "resident" })).toContain("常驻电脑");
  expect(header({ place: "local" })).toContain("本机");
});

it("the panel toggle prefers waiting items over plan progress, and dots while busy or waiting", () => {
  expect(header({ waiting: 2, plan: { done: 1, total: 3 } })).toContain('title="任务动态 · 2 件等你处理"');
  expect(header({ plan: { done: 1, total: 3 } })).toContain('title="任务动态 · 当前步骤 1/3"');
  expect(header()).not.toContain("workbench-toggle-dot");
  expect(header({ waiting: 1 })).toContain("workbench-toggle-dot waiting");
  expect(header({ busy: true })).toContain("停止");
});

it("a task pill replaces the panel toggle but keeps the stop button", () => {
  const html = header({ taskPill: <button>2 件在忙</button>, busy: true });
  expect(html).toContain("2 件在忙");
  expect(html).not.toContain("workbench-toggle");
  expect(html).toContain("停止");
});
