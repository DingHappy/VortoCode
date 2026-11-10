import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn() }));
vi.mock("../hooks/useDialogSurface", () => ({ useDialogSurface: () => ({ current: null }) }));

import { FeedbackDialog } from "./FeedbackDialog";

it("asks what happened, attaches the redacted log by default, keeps email as a fallback and cannot send an empty report", () => {
  const html = renderToStaticMarkup(<FeedbackDialog onClose={() => undefined} send={async () => 1} />);
  expect(html).toContain("遇到了什么问题");
  expect(html).toMatch(/<input type="checkbox" checked=""/);                 // 默认附日志
  expect(html).toContain("不含对话和文件内容");
  expect(html).toContain("改用邮件发送");
  expect(html).toMatch(/<button type="button" class="primary" disabled="">发送<\/button>/);   // 没写内容不能发
});
