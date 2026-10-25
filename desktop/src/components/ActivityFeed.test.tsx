import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { ActivityFeed } from "./ActivityFeed";
import type { FeedItem } from "../lib/activityFeed";
import { noticeFeedItem } from "../lib/activityFeed";

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

it("筛选无结果可提供准确空状态，不误称整个动态列表为空", () => {
  const html = renderToStaticMarkup(<ActivityFeed items={[]} scope="all" onOpen={() => {}} emptyMessage="已读取的动态中，暂时没有待处理事项。" />);
  expect(html).toContain('role="status"');
  expect(html).toContain("暂时没有待处理事项");
  expect(html).not.toContain("还没有任务");
});

it("没有跳转目标的通知可展开完整记录，不生成无效导航按钮，内容按纯文本呈现", () => {
  const notice = noticeFeedItem({ ts: "2026-10-11T12:00:00", source: "cron:digest", text: "✅ cron [digest] 通过\n摘要\n<script>alert(1)</script>" })!;
  const html = render("all", [notice]);
  expect(html).toContain("展开完整记录");
  expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  expect(html).not.toContain("<script>");
  expect(html).not.toContain('<button class="activity-feed-item');
  expect(html).toContain("定时任务");
});

it("外部保存的展开状态只打开对应记录，渲染不会调用导航或切换回调", () => {
  const onOpen = vi.fn(), onToggle = vi.fn();
  const html = renderToStaticMarkup(<ActivityFeed items={items.map(item => ({ ...item, session: undefined }))} scope="all" onOpen={onOpen}
    recordExpansion={{ openIds: new Set(["a"]), onToggle }} now={now} />);
  expect(html).toContain('<details class="activity-feed-record done" open="">');
  expect(html).toContain('<details class="activity-feed-record waiting">');
  expect(onOpen).not.toHaveBeenCalled();
  expect(onToggle).not.toHaveBeenCalled();
});

it("有目标的记录保留导航按钮，明确显示状态与准确日期", () => {
  const html = render("all", [{ ...items[1], dispatchId: "d1", at: "2026-10-09T11:00:00", statusLabel: "待确认下发", sourceLabel: "任务单 · VortoCode" }]);
  expect(html).toContain('<button class="activity-feed-item waiting"');
  expect(html).not.toContain("展开完整记录");
  expect(html).toContain('<span class="activity-feed-status">待确认下发</span>');
  expect(html).toContain("10月9日 11:00");
  expect(html).toContain('dateTime="2026-10-09T11:00:00"');
});
