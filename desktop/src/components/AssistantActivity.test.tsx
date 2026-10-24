import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { AssistantActivity } from "./AssistantActivity";
import type { FeedItem } from "../lib/activityFeed";
import { confirmationFeedItem } from "../lib/activityFeed";
import type { ActivitySource } from "../lib/activitySources";
const sources: ActivitySource[] = ["turns", "dispatches", "codexActions", "notices"].map(name => ({ name: name as ActivitySource["name"], status: "current", checkedAt: Date.now() }));

const items: FeedItem[] = [
  { id: "running", title: "正在核对构建", summary: "查看实际结果", status: "running", at: "2026-10-24T10:00:00Z" },
  { id: "waiting", title: "确认下一步", summary: "等你确认范围", status: "waiting", at: "2026-10-23T10:00:00Z" },
];

it("keeps activity and pending actions outside the initially closed workday overview", () => {
  const open = vi.fn();
  const html = renderToStaticMarkup(<AssistantActivity items={items} sources={sources} connected onOpen={open}
    overview={<p>概览中的数量与验收依据</p>}><button>原有任务操作</button></AssistantActivity>);
  expect(html).toContain('<details class="assistant-activity-overview"><summary>');
  expect(html).toContain("今日概览");
  expect(html).toContain("1 件待处理");
  expect(html.indexOf("概览中的数量与验收依据")).toBeLessThan(html.indexOf("</details>"));
  expect(html.indexOf("原有任务操作")).toBeGreaterThan(html.indexOf("</details>"));
  expect(html.indexOf("确认下一步")).toBeGreaterThan(html.indexOf("</details>"));
  expect(html.indexOf("确认下一步")).toBeLessThan(html.indexOf("正在核对构建"));
  expect(html).not.toContain("历史动态与对话记录");
  expect(html).not.toContain('role="radiogroup"');
  expect(html).toContain('aria-label="动态筛选"');
  expect(html).toContain('aria-pressed="true">全部动态');
  expect(html).toContain('aria-pressed="false">待处理');
  expect(open).not.toHaveBeenCalled();
});

it("counts the same visible pending records, including confirmations and Codex actions outside the overview", () => {
  const open = vi.fn();
  const pending = [...items, confirmationFeedItem({ id: "c1", text: "继续？", tainted: false }),
    { id: "codex_action:a1", title: "拟发消息", summary: "待你审阅", at: "", status: "waiting" as const, codexActionId: "a1" }];
  const html = renderToStaticMarkup(<AssistantActivity items={pending} sources={sources} connected overview={<p>折叠概览</p>} onOpen={open} />);
  expect(html).toContain("3 件待处理");
  const visible = html.slice(html.indexOf("</details>") + "</details>".length);
  expect(visible).toContain("当前对话有操作需要确认");
  expect(visible).toContain("拟发消息");
  expect(visible.match(/<button class="activity-feed-item waiting"/g)).toHaveLength(2);
  expect(open).not.toHaveBeenCalled();
});

it("marks retained history as unverified while offline without hiding it", () => {
  const html = renderToStaticMarkup(<AssistantActivity items={items} connected={false}
    overview={<p>概览</p>} onOpen={() => {}} />);
  expect(html).toContain("连接中断，以下动态为历史记录");
  expect(html).toContain("正在核对构建");
  expect(html).not.toContain("件待处理");
  expect(html).toContain("1+ 条历史待处理");
});

it("does not turn a failed empty read into a claim that there is no work", () => {
  const refresh = vi.fn();
  const html = renderToStaticMarkup(<AssistantActivity items={[]} sources={[{ name: "dispatches", status: "unavailable" }]}
    connected overview={<p>概览</p>} onOpen={() => {}} onRefresh={refresh} />);
  expect(html).toContain("数量可能不完整");
  expect(html).toContain("助理任务单：未能取得最新记录");
  expect(html).toContain("不能据此判断没有事项");
  expect(html).not.toContain("还没有任务");
  expect(html).toContain('aria-label="刷新动态"'); expect(refresh).not.toHaveBeenCalled();
});
