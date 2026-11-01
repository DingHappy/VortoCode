import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AssistantChanges, type AssistantChangeClient } from "./AssistantChanges";

const client: AssistantChangeClient = {
  listAssistantChanges: async () => [],
  undoAssistantChange: async () => ({ ok: true, detail: "", not_recallable: [] }),
};

it("没有连接或没有读写端时不显示这一段", () => {
  expect(renderToStaticMarkup(<AssistantChanges client={null} connected />)).toBe("");
  expect(renderToStaticMarkup(<AssistantChanges client={client} connected={false} />)).toBe("");
});

it("连上后先说明正在读取，不把还没读到当成没有改动", () => {
  const html = renderToStaticMarkup(<AssistantChanges client={client} connected />);
  expect(html).toContain("我替你做了什么");
  expect(html).toContain("正在读取改动记录");
});
