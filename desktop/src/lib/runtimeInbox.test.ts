import { describe, expect, it } from "vitest";

import {
  STARTING_GRACE_MS,
  classifyReachability,
  runtimeInboxSubtitle,
  summarizeRuntimeInboxes,
} from "./runtimeInbox";

const NOW = 1_700_000_000_000;

const counts = (over: Partial<Record<string, number>> = {}) => ({
  decisions: 0,
  hook_issues: 0,
  goals_blocked: 0,
  goals_active: 0,
  tasks_attention: 0,
  tasks_active: 0,
  sessions_working: 0,
  sessions_queued: 0,
  ...over,
}) as never;

describe("可达性归类", () => {
  it("没有错误就是正常", () => {
    expect(classifyReachability({ firstSeenAt: NOW, everReachable: false }, NOW)).toBe("ok");
  });

  it("刚出现、还没连上过 → 启动中，不是失联", () => {
    // 真机：加完项目后 runtime 冷启动那几秒，界面直接报「1 个失联」。
    const item = { error: "连接被拒绝", firstSeenAt: NOW, everReachable: false };
    expect(classifyReachability(item, NOW + 3_000)).toBe("starting");
  });

  it("超过宽限期仍连不上 → 失联", () => {
    const item = { error: "连接被拒绝", firstSeenAt: NOW, everReachable: false };
    expect(classifyReachability(item, NOW + STARTING_GRACE_MS + 1)).toBe("unreachable");
  });

  it("连上过又断了 → 立刻算失联，不再宽限", () => {
    const item = { error: "连接被拒绝", firstSeenAt: NOW, everReachable: true };
    expect(classifyReachability(item, NOW + 100)).toBe("unreachable");
  });
});

describe("汇总与副标题", () => {
  it("启动中与失联分开计数", () => {
    const summary = summarizeRuntimeInboxes([
      { error: "", firstSeenAt: NOW, everReachable: true, counts: counts({ sessions_working: 1 }) },
      { error: "拒绝", firstSeenAt: NOW, everReachable: false, counts: null },
      { error: "拒绝", firstSeenAt: NOW - 60_000, everReachable: true, counts: null },
    ], NOW + 1_000);
    expect(summary).toMatchObject({ runtimes: 3, starting: 1, unreachable: 1, working: 1 });
  });

  it("副标题优先说要处理的，其次失联，再次启动中", () => {
    const base = { runtimes: 2, unreachable: 0, starting: 0, actionable: 0, working: 0, queued: 0, goals: 0, tasks: 0 };
    expect(runtimeInboxSubtitle({ ...base, actionable: 3, unreachable: 1 })).toContain("3 项待处理");
    expect(runtimeInboxSubtitle({ ...base, unreachable: 1, starting: 1 })).toContain("1 个失联");
    expect(runtimeInboxSubtitle({ ...base, starting: 1 })).toContain("1 个启动中");
    expect(runtimeInboxSubtitle({ ...base, working: 2 })).toContain("2 个执行中");
  });
});
