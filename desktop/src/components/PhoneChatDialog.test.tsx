import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { PhoneChatDialog, phoneConversationMessages } from "./PhoneChatDialog";
import type { PhoneChat } from "../types";

const chat: PhoneChat = { sid: "telegram-chat", title: "📱 发布核对", transcript: [
  { role: "user", text: "检查一下", rid: "r1" },
  { role: "assistant", text: "**已记录**", rid: "r1" },
  { role: "user", text: "旧消息" },
] };

it("preserves exact recorded rids and gives duplicate-role entries distinct rendering keys", () => {
  const messages = phoneConversationMessages({ ...chat, transcript: [...chat.transcript, chat.transcript[0]] });
  expect(messages.map(item => item.rid)).toEqual(["r1", "r1", undefined, "r1"]);
  expect(new Set(messages.map(item => item.id)).size).toBe(4);
  expect(messages.map(item => item.role)).toEqual(["user", "assistant", "user", "user"]);
});

it("renders a named native dialog with read-only history and no conversation input", () => {
  const html = renderToStaticMarkup(<PhoneChatDialog chat={chat} focusedRid="r1" onClose={() => {}} />);
  expect(html).toContain("<dialog"); expect(html).toContain("aria-labelledby=");
  expect(html).toContain("这里只读"); expect(html).toContain("请在手机上回复");
  expect(html).toContain('data-turn-rid="r1"'); expect(html).toContain("<strong>已记录</strong>");
  expect(html).toContain('aria-label="关闭手机聊天"');
  for (const input of ["<textarea", "<input", "<form", 'contenteditable="true"']) expect(html).not.toContain(input);
});

it("keeps recorded turn keys stable when the bounded snapshot drops earlier turns", () => {
  const older = { role: "user", rid: "older", text: "较早消息" };
  const before = phoneConversationMessages({ ...chat, transcript: [older, ...chat.transcript] });
  const after = phoneConversationMessages(chat);
  expect(after.slice(0, 2).map(item => item.id)).toEqual(before.slice(1, 3).map(item => item.id));
});

it("escapes titles, text and rid attributes instead of executing recorded markup", () => {
  const html = renderToStaticMarkup(<PhoneChatDialog chat={{ ...chat, title: '<script>title</script>',
    transcript: [{ role: "user", rid: '\" onclick=\"alert(1)', text: '<script>text</script>' }] }} onClose={() => {}} />);
  expect(html).not.toContain("<script>"); expect(html).not.toContain(' onclick="');
  expect(html).toContain("&lt;script&gt;text&lt;/script&gt;");
});

it("uses a snapshot-specific empty message without claiming the phone never had a conversation", () => {
  const html = renderToStaticMarkup(<PhoneChatDialog chat={{ ...chat, transcript: [] }} onClose={() => {}} />);
  expect(html).toContain("当前没有可显示的聊天记录"); expect(html).not.toContain("还没有聊过");
});

it("offline variant explains where the chat lives and carries its own actions", () => {
  const html = renderToStaticMarkup(<PhoneChatDialog variant="offline" owner={null} onClose={() => {}}
    chat={{ sid: "desk-1", title: "周三开会", transcript: [{ role: "user", text: "帮我记下" }] }}
    actions={<button>带回常驻电脑</button>} />);
  expect(html).toContain("周三开会 · 本机离线时的聊天");
  expect(html).toContain("常驻助理不知道");
  expect(html).toContain('aria-label="关闭本机聊天"');
  expect(html).toContain("带回常驻电脑");
  expect(html).not.toContain("请在手机上回复");
  for (const input of ["<textarea", "<input", "<form"]) expect(html).not.toContain(input);
});
