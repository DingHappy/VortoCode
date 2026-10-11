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
