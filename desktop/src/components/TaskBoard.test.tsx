import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { TaskBoard } from "./TaskBoard";
import type { DesktopRuntimeInbox } from "../types";

const source: DesktopRuntimeInbox = { runtimeId: "a", label: "项目", scope: "project", baseUrl: "", checkedAt: 1, error: "offline",
  snapshot: { version: 1, scope: "project", generated_at: "then", counts: {} as never, sessions: [], goals: [], decisions: [],
    tasks: [{ id: "done", status: "done", prompt: "历史结果", detail: "", owner_session: "owner", goal_id: "", plan_id: "", branch: "", updated: "" }] } };
const render = (sources: DesktopRuntimeInbox[]) => renderToStaticMarkup(<TaskBoard sources={sources} currentRuntimeId="a" onOpen={() => { throw new Error("render must not navigate"); }} onOpenGoals={() => { throw new Error("render must not start goals"); }} />);
it("stale cards and Goal entry are disabled while retained evidence remains visible", () => {
  const html = render([source]);
  expect(html).toContain("状态未知"); expect(html).toContain("历史结果");
  expect(html.match(/disabled=""/g)).toHaveLength(2);
  expect(html).toContain("不是完整台账"); expect(html).toContain("执行结束");
});
it("General and missing sources cannot expose an executable Goal entry", () => {
  expect(render([])).toContain('disabled=""');
  expect(render([{ ...source, error: "", scope: "general" }])).toContain('disabled=""');
});
it("unknown snapshot versions produce an unknown notice without cards", () => {
  const html = render([{ ...source, error: "", snapshot: { ...source.snapshot!, version: 2 } }]);
  expect(html).toContain("摘要版本不支持"); expect(html).not.toContain("历史结果");
});
it("recovered failures remain explicit history with pending handling, not successful cards", () => {
  const historySource: DesktopRuntimeInbox = { ...source, error: "", snapshot: { ...source.snapshot!, tasks: [{
    ...source.snapshot!.tasks[0], status: "interrupted", lifecycle: {version: 1, execution: "interrupted", kind: "dev", review: "not_applicable",
      handling: "pending", validity: "not_applicable", round: null, revision: "a".repeat(64), column: "history", label: "已由恢复任务接续", recovered_by: "task-new"},
  }] } };
  const html = render([historySource]);
  expect(html).toContain("已恢复的历史任务"); expect(html).toContain("task-new");
  expect(html).toContain("仍需核对"); expect(html).toContain("恢复不证明验收或目标达成");
});
it("existing task Goal links have a separate exact navigation entry without execution", () => {
  const linked: DesktopRuntimeInbox = {...source, error: "", snapshot: {...source.snapshot!, tasks: [{...source.snapshot!.tasks[0], goal_id: "goal-outside-summary"}]}};
  const html = render([linked]);
  expect(html).toContain("查看关联 Goal"); expect(html).toContain("goal-outside-summary");
  expect(html).not.toContain("开始执行");
});
