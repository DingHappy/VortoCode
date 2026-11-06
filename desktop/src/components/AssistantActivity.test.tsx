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

it("shows decisions and a progress summary without repeating routine process rows", () => {
  const open = vi.fn();
  const html = renderToStaticMarkup(<AssistantActivity items={items} sources={sources} connected onOpen={open}>
    <button>原有任务操作</button></AssistantActivity>);
  expect(html).not.toContain("今日概览");
  expect(html).not.toContain("<details");
  expect(html).not.toContain('aria-label="动态筛选"');
  expect(html).not.toContain("最近完整核对");
  expect(html).toContain("<h5>等你处理 · 1</h5>");
  expect(html.indexOf("原有任务操作")).toBeLessThan(html.indexOf("确认下一步"));
  expect(html).not.toContain("正在核对构建");
  expect(html).toContain("已读取的进行中记录：1 条，过程已收起。");
  expect(html).toContain("查看全部记录（2）");
  expect(open).not.toHaveBeenCalled();
});

it("counts confirmations and Codex actions in the same waiting group", () => {
  const open = vi.fn();
  const pending = [...items, confirmationFeedItem({ id: "c1", text: "继续？", tainted: false }),
    { id: "codex_action:a1", title: "拟发消息", summary: "待你审阅", at: "", status: "waiting" as const, codexActionId: "a1" }];
  const html = renderToStaticMarkup(<AssistantActivity items={pending} sources={sources} connected onOpen={open} />);
  expect(html).toContain("<h5>等你处理 · 3</h5>");
  expect(html).toContain("当前对话有操作需要确认");
  expect(html).toContain("拟发消息");
  expect(html.match(/<button class="activity-feed-item waiting"/g)).toHaveLength(3);
  expect(open).not.toHaveBeenCalled();
});

it("marks retained history with the time it was read while offline, without hiding it", () => {
  const read = new Date(2026, 9, 27, 10, 42).getTime();
  const html = renderToStaticMarkup(<AssistantActivity items={items} connected={false}
    sources={[{ name: "turns", status: "current", checkedAt: read }]} onOpen={() => {}} />);
  expect(html).toContain("下面是 10:42 读到的动态，连上之后再核对现在的进度。");
  expect(html).toContain("已读取的进行中记录：1 条");
  expect(html).toContain("确认下一步");
  expect(html).not.toContain("动态来源待核对");
  const unknown = renderToStaticMarkup(<AssistantActivity items={items} connected={false} onOpen={() => {}} />);
  expect(unknown).toContain("下面是上次读到的动态");
});

it("preserves failures and honest result labels in the default summary", () => {
  const html = renderToStaticMarkup(<AssistantActivity connected sources={sources} onOpen={() => {}} items={[
    { id: "failed", status: "failed", title: "需要核对来源", summary: "请求失败", at: "" },
    { id: "result", status: "done", dispatchId: "d", title: "本轮改动", summary: "等待审查", statusLabel: "执行结束 · 待审查", at: "" },
    { id: "chat", status: "done", title: "普通聊天标题", summary: "已回复", at: "" },
  ]} />);
  expect(html).toContain("需要核对来源");
  expect(html).toContain("执行结束 · 待审查");
  expect(html).not.toContain("普通聊天标题");
  expect(html).not.toContain("已验收");
});

it("never paints an unreachable retained completion as a fresh success", () => {
  const html = renderToStaticMarkup(<AssistantActivity connected sources={sources} onOpen={() => {}} items={[
    { id: "old", status: "done", dispatchId: "d", requiresAttention: true, title: "上次执行结束", summary: "来源失联", statusLabel: "状态待核对", at: "" },
  ]} />);
  expect(html).toContain("需要核对 · 1");
  expect(html).toContain('class="activity-feed-item stopped"');
  expect(html).not.toContain('class="activity-feed-item done"');
  expect(html).not.toContain("最近结果");
});

it("does not turn a failed empty read into a claim that there is no work", () => {
  const refresh = vi.fn();
  const html = renderToStaticMarkup(<AssistantActivity items={[]} sources={[{ name: "dispatches", status: "unavailable" }]}
    connected onOpen={() => {}} onRefresh={refresh} />);
  expect(html).toContain("数量可能不完整");
  expect(html).toContain("助理任务单：未能取得最新记录");
  expect(html).toContain("不能据此判断没有事项");
  expect(html).not.toContain("还没有任务");
  expect(html).toContain('aria-label="刷新动态"'); expect(refresh).not.toHaveBeenCalled();
});

it("links to the single main-chat inbox while preserving exceptions and selected task actions", () => {
  const html = renderToStaticMarkup(<AssistantActivity connected sources={sources} onOpen={() => {}}
    waitingInbox={{ count: 3, uncertain: true, onOpen: () => {} }} items={[...items,
      { id: "failed", status: "failed", title: "来源失联需核对", summary: "未取得最新结果", at: "" },
    ]}><button>当前确认卡</button></AssistantActivity>);
  expect(html).toContain("3+ 件在主要聊天等你处理");
  expect(html).not.toContain('<section aria-label="等你处理">');
  expect(html).not.toContain("确认下一步");
  expect(html).toContain("来源失联需核对");
  expect(html).toContain("当前确认卡");
  expect(html).toContain("查看全部记录（3）");
});

it("does not claim an empty inbox when only a team is waiting in main chat", () => {
  const html = renderToStaticMarkup(<AssistantActivity connected sources={sources} onOpen={() => {}} items={[]}
    waitingInbox={{ count: 1, uncertain: false, onOpen: () => {} }} />);
  expect(html).toContain("1 件在主要聊天等你处理");
  expect(html).not.toContain("暂未发现待处理事项");
});
