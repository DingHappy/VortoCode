import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { timelineExpanded, TurnTimeline } from "./TurnTimeline";
import type { TurnActivity } from "../types";
const items: TurnActivity[] = [{ id: "tool", kind: "tool", status: "completed", label: "读取配置", result: "旧结果仍可查看" }];
it("only collapses after actual output, not just when work stops", () => {
  expect(timelineExpanded(items, false, false)).toBe(true);
  expect(timelineExpanded(items, true, true)).toBe(true);
  const output = renderToStaticMarkup(<TurnTimeline items={items} active={false} hasOutput />);
  expect(output).toContain('aria-expanded="false"');
  expect(output).not.toContain("timeline-list");
});
it("keeps interrupted, blocked and failed work open even after output", () => {
  for (const status of ["failed", "blocked", "cancelled", "timed_out", "running"]) {
    expect(timelineExpanded([{ ...items[0], status }], false, true)).toBe(true);
  }
  const output = renderToStaticMarkup(<TurnTimeline items={[{ ...items[0], status: "failed" }]} active={false} hasOutput />);
  expect(output).toContain("需要关注");
  expect(output).toContain("旧结果仍可查看");
});

it("shows a recap of the finished round in the collapsed heading, and a plain count while working", () => {
  const round: TurnActivity[] = [
    { id: "e1", kind: "tool", status: "completed", label: "编辑", name: "edit_file", args: { path: "a.py" } },
    { id: "t1", kind: "tool", status: "completed", label: "测试", name: "run_tests", result: "测试未过 ✗（pytest）" },
  ];
  const done = renderToStaticMarkup(<TurnTimeline items={round} active={false} hasOutput />);
  expect(done).toContain("改了 1 个文件 · 跑测试 1 次（1 次没过）");
  const working = renderToStaticMarkup(<TurnTimeline items={round} active hasOutput={false} />);
  expect(working).toContain("2 个工具");
  expect(working).not.toContain("改了 1 个文件");
});
