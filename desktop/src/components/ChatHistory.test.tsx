import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { ConversationMessage, TurnActivity } from "../types";
import { ChatHistory } from "./ChatHistory";

const messages: ConversationMessage[] = [
  { id: "u1", role: "user", text: "检查项目", rid: "r1", source: "telegram", images: ["data:image/png;base64,AA=="] },
  { id: "a1", role: "assistant", text: "**检查完成**", rid: "r1", source: "telegram" },
];
const activity: TurnActivity = { id: "t1", kind: "tool", status: "completed", label: "读取配置", result: "读取结果" };
const props = { messages, activityGroups: new Map([["r1", [activity]]]), busy: false,
  activeTurnRid: null, assistant: true, name: "助理", avatar: null };

it("preserves phone attribution, attachments, Markdown and the completed turn timeline", () => {
  const html = renderToStaticMarkup(<ChatHistory {...props} />);
  for (const text of ["Telegram", "回复在手机上", "发送的图片 1", "<strong>检查完成</strong>", "助理", 'aria-expanded="false"']) {
    expect(html).toContain(text);
  }
  expect(html).not.toContain("读取结果");
});

it("does not collapse a turn based on another turn's output or an empty reply", () => {
  for (const reply of [{ ...messages[1], rid: "r2" }, { ...messages[1], text: " \n" }]) {
    const html = renderToStaticMarkup(<ChatHistory {...props} messages={[messages[0], reply]} />);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("读取结果");
  }
});

it("keeps current and failed work visible even if the turn has output", () => {
  const active = renderToStaticMarkup(<ChatHistory {...props} busy activeTurnRid="r1" />);
  expect(active).toContain("正在工作");
  expect(active).toContain("读取结果");
  const failed = renderToStaticMarkup(<ChatHistory {...props}
    activityGroups={new Map([["r1", [{ ...activity, status: "failed" }]]])} />);
  expect(failed).toContain("需要关注");
  expect(failed).toContain("读取结果");
});

it("uses workbench authors outside assistant mode", () => {
  const html = renderToStaticMarkup(<ChatHistory {...props} assistant={false} />);
  expect(html).toContain('<div class="message-author">你</div>');
  expect(html).toContain('<div class="message-author">VortoCode</div>');
});
