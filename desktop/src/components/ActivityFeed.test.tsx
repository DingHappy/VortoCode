import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ActivityFeed } from "./ActivityFeed";
import type { FeedItem } from "../lib/activityFeed";

const now = new Date(2026, 9, 11, 13, 0);
const items: FeedItem[] = [
  { id: "a", title: "本对话的任务", summary: "已完成", status: "done", at: "2026-10-11T12:00:00", session: "sid-abc" },
  { id: "b", title: "别的对话的任务", summary: "等你回复：选哪个？", status: "waiting", at: "2026-10-11T11:00:00", session: "sid-other" },
];
const render = (scope: "all" | "session", list = items) => renderToStaticMarkup(
  <ActivityFeed items={list} scope={scope} onScope={() => {}} currentSession="abc" onOpen={() => {}} now={now} />);

it("全部：等你处理排在今天之前", () => {
  const html = render("all");
  expect(html.indexOf("等你处理")).toBeLessThan(html.indexOf("今天"));
  expect(html.indexOf("别的对话的任务")).toBeLessThan(html.indexOf("本对话的任务"));
});

it("本对话：后端的 sid- 前缀与前端裸 id 能对上，只留本对话发起的", () => {
  const html = render("session");
  expect(html).toContain("本对话的任务");
  expect(html).not.toContain("别的对话的任务");
});

it("没有任务时给一句说明，不留空白", () => {
  expect(render("all", [])).toContain("还没有任务");
  expect(render("session", [])).toContain("这个对话还没有交代出去的任务");
});
