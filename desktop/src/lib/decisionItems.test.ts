import { expect, it } from "vitest";

import type { DecisionItem, PrDeliverySnapshot, SessionHookIssue, SessionSummary } from "../types";
import { withLocalDecisions } from "./decisionItems";

const queued = { id: "d1", kind: "confirmation", severity: "medium", title: "待确认" } as DecisionItem;
const issue = (patch: Partial<SessionHookIssue>) => ({ id: "h1", name: "format", event: "post_tool_use", ...patch }) as SessionHookIssue;
const session = (items: SessionHookIssue[]): SessionSummary => ({
  sid: "s1", title: "修登录", messages: 3, updated: 1_700_000_000,
  hook_issues: { count: items.length, items, truncated: false },
});
const pr = (patch: Partial<PrDeliverySnapshot>): PrDeliverySnapshot => ({
  ok: true, pr: 42, branch: "vorto/x", comments: [], checks: [], failing_checks: [],
  summary: { total: 0, failed: 0, pending: 0, passed: 0 }, ...patch,
});

it("keeps the runtime queue first and adds session hook issues", () => {
  const items = withLocalDecisions([queued], [session([issue({ tool: "edit_file", error: "ruff 失败" })])], null);
  expect(items[0]).toBe(queued);
  expect(items[1]).toMatchObject({
    id: "hook:s1:h1", kind: "hook", title: "Hook 需要处理 · format", session_id: "s1", action: "open_session", can_dismiss: true,
    detail: "修登录 · post_tool_use · edit_file\nruff 失败", created: new Date(1_700_000_000_000).toISOString(),
  });
});

it("adds failing CI checks and unresolved review comments, but only from a readable PR", () => {
  const snapshot = pr({
    failing_checks: [{ id: "c1", name: "unit", workflow: "ci", conclusion: "failure", failing: true, pending: false }],
    comments: [{ author: "reviewer", body: "这里要判空", path: "src/a.ts", line: 9, resolved: false },
      { author: "reviewer", body: "整体 OK", resolved: false }],
  });
  const items = withLocalDecisions([], [], snapshot);
  expect(items.map(item => [item.id, item.kind, item.can_dismiss])).toEqual([
    ["pr-check:c1", "pr_check", false],
    ["pr-review:42:src/a.ts:9:0", "pr_review", false],
    ["pr-review:42:general:0:1", "pr_review", false],
  ]);
  expect(items[0].detail).toBe("ci · failure");
  expect(items[1].detail).toBe("src/a.ts:9 · @reviewer: 这里要判空");
  expect(withLocalDecisions([], [], { ...snapshot, ok: false })).toEqual([]);
});
