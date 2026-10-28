import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { ConversationHome, FirstRunCard, greeting } from "./ConversationHome";

const home = (patch: Partial<Parameters<typeof ConversationHome>[0]> = {}) => renderToStaticMarkup(
  <ConversationHome starting={false} showStarting={false} assistant={false} {...patch} />);

it("while the engine starts, the greeting is a placeholder that appears only after the delay", () => {
  expect(home({ starting: true })).toContain('class="home-starting pending" aria-hidden="true"');
  expect(home({ starting: true, showStarting: true })).toContain("正在启动本地引擎…");
  expect(home({ starting: true, assistant: true })).not.toContain("今天想交代点什么");
});

it("assistant mode greets by time of day in one line; the workbench has its own greeting", () => {
  expect(home({ assistant: true, now: new Date(2026, 9, 28, 15, 0) })).toContain('<h1 class="vc-greeting">下午好，今天想交代点什么？</h1>');
  expect(home({ assistant: true })).not.toContain("<p>");
  expect(home({})).toContain("开始一项工作");
  expect([7, 12, 15, 21, 2].map(hour => greeting(new Date(2026, 9, 28, hour)))).toEqual(["上午好", "中午好", "下午好", "晚上好", "晚上好"]);
});

it("the first-run card offers the member model or your own", () => {
  const noop = () => {};
  expect(renderToStaticMarkup(<FirstRunCard signedIn onAccount={noop} onModel={noop} />)).toContain("使用会员模型");
  expect(renderToStaticMarkup(<FirstRunCard signedIn={false} onAccount={noop} onModel={noop} />)).toContain("登录 VortoCode 账号");
});
