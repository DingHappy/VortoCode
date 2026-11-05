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

it("例行巡视只留最新一条，其余折叠；有待决定动作或异常的照常显示；展开历史时全列", async () => {
  const { foldRoutine } = await import("./AssistantTimeline");
  const notice = (id: string, title: string, over: object = {}) => ({ id, at: "2026-11-05T06:00:00Z", kind: "notice" as const,
    title, detail: "", status: "", tainted: false, actions: [], ...over });
  const items = [notice("p3", "🧭 Codex 进度（半小时巡视）"), notice("x", "早报"), notice("p2", "🧭 Codex 进度（半小时巡视）"),
    notice("p1", "🧭 Codex 进度（半小时巡视）", { status: "读取失败，需核对" }), notice("p0", "🧭 Codex 进度（半小时巡视）")];
  const folded = foldRoutine(items, false);
  expect(folded.items.map(item => item.id)).toEqual(["p3", "x", "p1"]);
  expect(folded.folded).toBe(2);
  expect(foldRoutine(items, true)).toEqual({ items, folded: 0 });
});
