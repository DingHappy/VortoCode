import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AssistantTimeline, type TimelineClient } from "./AssistantTimeline";

const client: TimelineClient = {
  getTimeline: async () => ({ items: [], unavailable: [], generated_at: "" }),
  resumeGoalAdvance: async () => undefined,
  undoAssistantChange: async () => ({ ok: true, detail: "", not_recallable: [] }),
};

it("没连上时不显示；连上后先说明正在读取，不把还没读到当成没有动静", () => {
  expect(renderToStaticMarkup(<AssistantTimeline client={null} connected />)).toBe("");
  expect(renderToStaticMarkup(<AssistantTimeline client={client} connected={false} />)).toBe("");
  const html = renderToStaticMarkup(<AssistantTimeline client={client} connected />);
  expect(html).toContain("时间线");
  expect(html).toContain("正在读取时间线");
});
