import { describe, expect, it } from "vitest";

import type { DecisionItem, DesktopRuntimeInbox, RuntimeInboxSnapshot } from "../types";
import { notificationCandidates, takeUnseen } from "./decisionNotifications";

const decision = (id: string, severity: string, kind = "confirmation") => ({ id, kind, severity, title: id }) as DecisionItem;
const inbox = (runtimeId: string, decisions: DecisionItem[]): DesktopRuntimeInbox => ({
  runtimeId, scope: "project", label: runtimeId, baseUrl: "", error: "", checkedAt: 0,
  snapshot: { decisions } as unknown as RuntimeInboxSnapshot,
});

describe("notification candidates", () => {
  it("takes urgent inbox decisions from every runtime and only PR/CI decisions from the current workspace", () => {
    const ids = notificationCandidates(
      [inbox("a", [decision("d1", "high"), decision("d2", "low")]), inbox("b", [decision("d1", "critical")]),
        { ...inbox("c", []), snapshot: null }],
      [decision("pr-check:1", "high", "pr_check"), decision("hook:x", "high", "hook"), decision("pr-review:1", "medium", "pr_review")],
      "rt-current",
    ).map(item => item.id);
    expect(ids).toEqual(["runtime:a:d1", "runtime:b:d1", "runtime:rt-current:pr-check:1"]);
  });
});

describe("takeUnseen", () => {
  it("while establishing a baseline, records everything and notifies nothing", () => {
    const seen = new Set<string>();
    expect(takeUnseen([{ id: "x" }, { id: "y" }], seen, true)).toEqual([]);
    expect([...seen]).toEqual(["x", "y"]);
  });

  it("afterwards, returns only ids not seen before and remembers them", () => {
    const seen = new Set(["x"]);
    expect(takeUnseen([{ id: "x" }, { id: "z" }], seen, false)).toEqual(["z"]);
    expect(takeUnseen([{ id: "x" }, { id: "z" }], seen, false)).toEqual([]);
  });
});
