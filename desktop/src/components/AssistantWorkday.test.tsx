import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { AssistantWorkdayView } from "./AssistantWorkday";
import type { AssistantWorkday } from "../lib/assistantWorkday";
const now = new Date(2026, 9, 23, 12);
const render = (model: AssistantWorkday, connected = true) => renderToStaticMarkup(
  <AssistantWorkdayView model={model} now={now} connected={connected} onOpen={() => {}} onRefresh={() => {}} />);
it("renders the four work questions and actionable provenance instead of a chat completion count", () => {
  const item = { id: "g1", title: "Release", detail: "Review the build", source: "个人目标", at: now.toISOString(),
    state: "running" as const, target: { kind: "goal" as const, id: "g1" } };
  const html = render({ today: [item], progress: [item], completed: [], decisions: [], warnings: [] });
  for (const label of ["今天的事", "正在推进", "已核实完成", "等你决定"]) expect(html).toContain(label);
  expect(html).toContain("Review the build"); expect(html).toContain("查看目标与验收");
  expect(html).toContain('aria-label="查看Release"'); expect(html).toContain('aria-pressed="true"');
});
it("disconnected or partial data is explicitly unknown, never an all-clear empty state", () => {
  const html = render({ today: [], progress: [], completed: [], decisions: [], warnings: ["来源未读取"] }, false);
  expect(html).toContain("连接中断，当前进展待核对"); expect(html).toContain("暂未取得可展示的记录");
  expect(html).toContain('disabled=""'); expect(html).not.toContain("今天没有待办");
});
it("escapes source text and bounds a long list without hiding its total", () => {
  const items = Array.from({ length: 8 }, (_, index) => ({ id: String(index), title: `<script>${index}</script>`, detail: "x",
    source: "Project", at: "", state: "review" as const, target: { kind: "dispatch" as const, id: String(index) } }));
  const html = render({ today: items, progress: [], completed: [], decisions: items, warnings: [] });
  expect(html).not.toContain("<script>"); expect(html).toContain("&lt;script&gt;");
  expect(html).toContain("查看其余 3 项"); expect(html.match(/class="workday-item review"/g)).toHaveLength(5);
});
it("connected pending reads ask the user to wait instead of reconnecting", () => {
  const html = renderToStaticMarkup(<AssistantWorkdayView model={{ today: [], progress: [], completed: [], decisions: [], warnings: ["来源待核对"] }}
    now={now} connected busy onOpen={() => {}} onRefresh={() => {}} />);
  expect(html).toContain("正在核对来源，请稍候");
  expect(html).not.toContain("恢复连接后");
});
it("failed schedules and pending memories link to where they are handled", () => {
  const schedule = { id: "schedule:j", title: "早报", detail: "最近一次失败", source: "定时", at: now.toISOString(),
    state: "decision" as const, target: { kind: "schedule" as const, id: "j" } };
  const memory = { id: "memory:proposals", title: "2 条记忆等你确认", detail: "例如：x", source: "记忆", at: now.toISOString(),
    state: "decision" as const, target: { kind: "memory" as const, id: "proposals" } };
  const html = render({ today: [schedule, memory], progress: [], completed: [], decisions: [schedule, memory], warnings: [] });
  expect(html).toContain("查看定时与运行记录"); expect(html).toContain("去确认记忆");
});
