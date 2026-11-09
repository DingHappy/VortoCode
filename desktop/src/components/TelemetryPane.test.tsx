import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => undefined) }));
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn() }));

import { TelemetryPane } from "./SettingsModal";

it("shows error reports on and usage statistics off until the saved settings load, and says what is sent", () => {
  const html = renderToStaticMarkup(<TelemetryPane load={() => new Promise(() => undefined)} save={async (next) => next} />);
  expect(html).toContain("自动发送错误报告");
  expect(html).toContain("发送匿名使用统计");
  expect(html).toContain("不含对话、文件内容和账号信息");
  const boxes = [...html.matchAll(/<input type="checkbox"([^>]*)>/g)].map(match => match[1]);
  expect(boxes).toHaveLength(2);
  expect(boxes[0]).toContain("checked");
  expect(boxes[1]).not.toContain("checked");
  expect(boxes.every(attrs => attrs.includes("disabled"))).toBe(true);    // 读到本机设置之前不能点
  expect(html).toContain("隐私政策");
});
