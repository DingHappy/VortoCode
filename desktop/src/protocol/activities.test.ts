/**
 * protocol/activities.ts 的纯逻辑单测（B8-④b S1 拆分安全网）。
 * 喂 ProtocolEvent 帧 → 断映射出的 TurnActivity；不起 dev server、不联网。
 */
import { describe, expect, it } from "vitest";

import type { ProtocolEvent, TurnActivity } from "../types";
import {
  finishRunningActivities,
  hydrateActivities,
  protocolActivity,
  upsertActivity,
} from "./activities";

function ev(partial: Partial<ProtocolEvent>): ProtocolEvent {
  return partial as ProtocolEvent;
}

describe("protocolActivity", () => {
  it("把 agent_phase 映射成 phase 活动", () => {
    const activity = protocolActivity(ev({
      id: "p1",
      rid: "r1",
      type: "agent_phase",
      status: "running",
      label: "正在分析",
      phase: "thinking",
      duration_ms: 1200,
    }));
    expect(activity).toEqual({
      id: "p1",
      rid: "r1",
      kind: "phase",
      status: "running",
      label: "正在分析",
      phase: "thinking",
      durationMs: 1200,
    });
  });

  it("把 agent_tool 映射成 tool 活动（summary 优先于 name）", () => {
    const activity = protocolActivity(ev({
      id: "t1",
      type: "agent_tool",
      name: "read_file",
      summary: "读取 App.tsx",
      args: { path: "App.tsx" },
      result: 42,
      status: "completed",
    }));
    expect(activity?.kind).toBe("tool");
    expect(activity?.label).toBe("读取 App.tsx");
    expect(activity?.name).toBe("read_file");
    expect(activity?.args).toEqual({ path: "App.tsx" });
    expect(activity?.result).toBe("42");
  });

  it("把 agent_hook 映射成 hook 活动并保留 event/tool/error", () => {
    const activity = protocolActivity(ev({
      id: "h1",
      type: "agent_hook",
      name: "pre-commit",
      event: "PreToolUse",
      tool: "run_command",
      error: "boom",
    }));
    expect(activity?.kind).toBe("hook");
    expect(activity?.event).toBe("PreToolUse");
    expect(activity?.tool).toBe("run_command");
    expect(activity?.error).toBe("boom");
    expect(activity?.status).toBe("running"); // 缺省 status
  });

  it("缺 id 或未知类型时返回 null", () => {
    expect(protocolActivity(ev({ type: "agent_tool" }))).toBeNull();
    expect(protocolActivity(ev({ id: "x", type: "chat_delta" }))).toBeNull();
  });
});

describe("upsertActivity", () => {
  it("插入新活动", () => {
    const a: TurnActivity = { id: "1", kind: "tool", status: "running", label: "跑" };
    expect(upsertActivity([], a)).toEqual([a]);
  });

  it("命中同 id 时浅合并（新字段覆盖旧字段）", () => {
    const prev: TurnActivity[] = [{ id: "1", kind: "tool", status: "running", label: "跑" }];
    const next = upsertActivity(prev, { id: "1", kind: "tool", status: "completed", label: "跑完" });
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({ id: "1", status: "completed", label: "跑完" });
  });

  it("列表上限保留最近 300 条", () => {
    let acc: TurnActivity[] = [];
    for (let i = 0; i < 305; i += 1) {
      acc = upsertActivity(acc, { id: `id-${i}`, kind: "tool", status: "running", label: `l${i}` });
    }
    expect(acc).toHaveLength(300);
    expect(acc[0].id).toBe("id-5");
    expect(acc[acc.length - 1].id).toBe("id-304");
  });
});

describe("hydrateActivities", () => {
  it("把历史帧折叠成时间线（同 id 去重更新）", () => {
    const result = hydrateActivities([
      { id: "t1", type: "agent_tool", name: "grep", status: "running" },
      { id: "t1", type: "agent_tool", name: "grep", status: "completed", summary: "找到 3 处" },
      { type: "agent_tool" }, // 缺 id → 跳过
    ]);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ id: "t1", status: "completed", label: "找到 3 处" });
  });
});

describe("finishRunningActivities", () => {
  const base: TurnActivity[] = [
    { id: "a", rid: "r1", kind: "phase", status: "running", label: "正在工作" },
    { id: "b", rid: "r2", kind: "tool", status: "running", label: "正在调用" },
    { id: "c", rid: "r1", kind: "tool", status: "completed", label: "已完成" },
  ];

  it("completed 把 running 的「正在」改「已」，只碰匹配 rid", () => {
    const next = finishRunningActivities(base, "r1", "completed");
    expect(next[0]).toMatchObject({ status: "completed", label: "已工作" });
    expect(next[1].status).toBe("running"); // rid 不匹配，保持不动
    expect(next[2].label).toBe("已完成");
  });

  it("failed / cancelled 用工具语义文案", () => {
    const failed = finishRunningActivities(base, undefined, "failed");
    expect(failed[1].label).toBe("工具调用失败");
    const cancelled = finishRunningActivities(base, undefined, "cancelled");
    expect(cancelled[0].label).toBe("任务已取消");
    expect(cancelled[1].label).toBe("工具调用已取消");
  });
});
