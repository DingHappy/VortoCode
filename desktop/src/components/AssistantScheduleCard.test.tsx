import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AssistantScheduleCard } from "./AssistantScheduleCard";
import type { AssistantScheduleItem } from "../types";

const item: AssistantScheduleItem = { id: "cron-brief", kind: "cron", title: "brief", schedule: "every 1h", enabled: true, revision: "abc", scheduler_enabled: false, failures: 2 };
const props = { connected: true, onToggle: async () => {}, onHistory: async () => [] };
it("shows configured actions and distinguishes global scheduling from job enabled", () => {
  const html = renderToStaticMarkup(<AssistantScheduleCard item={item} {...props} />);
  for (const text of ["暂停", "执行记录", "总开关未开启", "连续失败 2 次"]) expect(html).toContain(text);
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
  expect(html).toContain("此运行端未配置目标提醒通知");
  expect(html).toContain("在个人目标卡中核对或暂停");
  expect(html).not.toContain("<button");
  expect(html).not.toContain("调度总开关未开启");
});
