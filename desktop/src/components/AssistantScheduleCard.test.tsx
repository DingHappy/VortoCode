import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AssistantScheduleCard } from "./AssistantScheduleCard";
import type { AssistantScheduleItem } from "../types";

const item: AssistantScheduleItem = { id: "cron-brief", kind: "cron", title: "brief", schedule: "every 1h", enabled: true, revision: "abc", scheduler_enabled: false, failures: 2 };
const props = { connected: true, onToggle: async () => {}, onHistory: async () => [] };
it("shows configured actions and distinguishes global scheduling from job enabled", () => {
  const html = renderToStaticMarkup(<AssistantScheduleCard item={item} {...props} />);
  for (const text of ["暂停", "执行记录", "不会按时自动跑", "连续失败 2 次"]) expect(html).toContain(text);
});
it("disconnected cards disable mutations and reads", () => {
  const html = renderToStaticMarkup(<AssistantScheduleCard item={{ ...item, enabled: false }} {...props} connected={false} />);
  expect(html).toContain("恢复");
  expect(html.match(/disabled=""/g)).toHaveLength(2);
});
it("environment-controlled items and legacy servers do not expose management", () => {
  for (const readOnly of [{ ...item, kind: "heartbeat" as const }, { ...item, revision: undefined }]) {
    const html = renderToStaticMarkup(<AssistantScheduleCard item={readOnly} {...props} />);
    expect(html).toContain("由运行端配置管理");
    expect(html).not.toContain("<button");
  }
});
it("personal reminders direct management to the goal card and do not expose cron actions", () => {
  const html = renderToStaticMarkup(<AssistantScheduleCard item={{ ...item, kind: "goal", id: "personal-demo" }} {...props} />);
  expect(html).toContain("目标提醒发不出去");
  expect(html).toContain("在个人目标卡中核对或暂停");
  expect(html).not.toContain("<button");
  expect(html).not.toContain("调度总开关未开启");
});
it.each([
  ["timed_out", "上次巡检超时"],
  ["cancelled", "上次巡检被中断"],
  ["running", "可能仍在执行或已中断"],
] as const)("shows %s without assuming a saved run is still live", (last_status, text) => {
  const html = renderToStaticMarkup(<AssistantScheduleCard item={{ ...item, kind: "watch", last_status }} {...props} />);
  expect(html).toContain(text);
  expect(html).not.toContain("<button");
});
it("reads schedule and run times in local words and marks environment items read-only once", () => {
  const now = new Date(2026, 9, 27, 21, 0);
  const html = renderToStaticMarkup(<AssistantScheduleCard now={now}
    item={{ ...item, kind: "watch", revision: undefined, scheduler_enabled: true, failures: 0, schedule: "每 1 分钟", last: "2026-10-27T20:25:00", next: "2026-10-28T09:00:00" }} {...props} />);
  for (const text of ["每分钟", "上次 今天 20:25 · 下次 明天 09:00", "只读", "已启用"]) expect(html).toContain(text);
  expect(html).not.toContain("T20:25");
});
