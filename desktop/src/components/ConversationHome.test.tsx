import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { ConversationHome, FirstRunCard } from "./ConversationHome";

const home = (patch: Partial<Parameters<typeof ConversationHome>[0]> = {}) => renderToStaticMarkup(
  <ConversationHome starting={false} showStarting={false} assistant={false} resident={false} {...patch} />);

it("while the engine starts, the greeting is a placeholder that appears only after the delay", () => {
  expect(home({ starting: true })).toContain('class="home-starting pending" aria-hidden="true"');
  expect(home({ starting: true, showStarting: true })).toContain("正在启动本地引擎…");
  expect(home({ starting: true, assistant: true })).not.toContain("有什么要交代的？");
});

it("assistant mode says where work continues and shows tips; the workbench has its own greeting", () => {
  expect(home({ assistant: true, resident: true, tips: <i>tips</i> })).toContain("可以合上电脑");
  expect(home({ assistant: true, tips: <i>tips</i> })).toContain("<i>tips</i>");
  expect(home({ tips: <i>tips</i> })).toContain("开始一项工作");
  expect(home({ tips: <i>tips</i> })).not.toContain("<i>tips</i>");
});

it("the first-run card offers the member model or your own", () => {
  const noop = () => {};
  expect(renderToStaticMarkup(<FirstRunCard signedIn onAccount={noop} onModel={noop} />)).toContain("使用会员模型");
  expect(renderToStaticMarkup(<FirstRunCard signedIn={false} onAccount={noop} onModel={noop} />)).toContain("登录 VortoCode 账号");
});
