import { describe, expect, it } from "vitest";

import type { GatewayClient } from "../gateway";
import type { AssistantConnector, AssistantMemory, AssistantScheduleItem, MemoryProposal } from "../types";
import { assistantPanelActions, type AssistantPanelDeps } from "./assistantPanelActions";
import { AssistantActionGate } from "./assistantTask";

const item = { id: "a", kind: "cron", title: "早报", schedule: "每天 9:00", enabled: true, revision: "r1" } as AssistantScheduleItem;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

/** 一个假的前台环境：client 和 scope 都能在请求途中被换掉。 */
function harness(client: Partial<GatewayClient>, overrides: Partial<AssistantPanelDeps> = {}) {
  const state = { client: client as GatewayClient | null, scope: "host-a", banners: [] as string[],
    schedule: null as AssistantScheduleItem[] | null, memories: [{ id: "m1", content: "住北京" }] as AssistantMemory[],
    proposals: [{ id: "p1" }, { id: "p2" }] as MemoryProposal[], confirms: [] as string[], refreshed: 0, invalidated: 0 };
  const deps: AssistantPanelDeps = {
    client: () => state.client,
    scope: () => state.scope,
    renderScope: "host-a",
    scheduleEpoch: { current: 0 },
    connectorReads: { invalidate: () => { state.invalidated += 1; } },
    actionGate: new AssistantActionGate(),
    refreshConnectorStatus: async () => { state.refreshed += 1; },
    refreshAudit: async () => undefined,
    confirm: async (text) => { state.confirms.push(text); return true; },
    openUrl: async () => undefined,
    copy: async () => undefined,
    prepareAvatar: async () => ({ mime: "image/png", data: "x" }) as never,
    setBanner: (text) => state.banners.push(text),
    setSchedule: (items) => { state.schedule = items; },
    setMemories: (next) => { state.memories = typeof next === "function" ? next(state.memories) : next; },
    setProposals: (next) => { state.proposals = typeof next === "function" ? next(state.proposals) : next; },
    setAvatar: () => undefined,
    ...overrides,
  };
  return { state, actions: assistantPanelActions(deps), deps };
}

describe("schedule", () => {
  it("drops a reply that arrives after you switched machines, and only the latest read wins", async () => {
    const slow = deferred<AssistantScheduleItem[]>();
    const { state, actions } = harness({ getAssistantSchedule: () => slow.promise });
    const pending = actions.scheduleRefresh();
    state.scope = "host-b";
    slow.resolve([item]);
    await pending;
    expect(state.schedule).toBeNull();

    const reads = [deferred<AssistantScheduleItem[]>(), deferred<AssistantScheduleItem[]>()];
    let call = 0;
    const second = harness({ getAssistantSchedule: () => reads[call++].promise });
    const first = second.actions.scheduleRefresh();
    const latest = second.actions.scheduleRefresh();
    reads[1].resolve([{ ...item, title: "新" }]);
    reads[0].resolve([{ ...item, title: "旧" }]);
    await Promise.all([first, latest]);
    expect(second.state.schedule?.[0].title).toBe("新");
  });

  it("toggles, reloads every card, and says so when the reload fails", async () => {
    const calls: unknown[] = [];
    const { state, actions } = harness({
      setScheduleEnabled: async (...args: unknown[]) => { calls.push(args); },
      getAssistantSchedule: async () => [{ ...item, enabled: false }],
    });
    await actions.scheduleToggle(item);
    expect(calls).toEqual([["早报", false, "r1"]]);
    expect(state.schedule?.[0].enabled).toBe(false);
    const broken = harness({ setScheduleEnabled: async () => undefined, getAssistantSchedule: async () => { throw new Error("x"); } });
    await expect(broken.actions.scheduleToggle(item)).rejects.toThrow("启停配置已保存，但刷新失败");
    await expect(harness({}).actions.scheduleToggle({ ...item, revision: undefined })).resolves.toBeUndefined();
  });
});

describe("connectors", () => {
  const github = { name: "github", local_revocation_supported: true } as AssistantConnector;

  it("asks before disconnecting, then refreshes and shows the receipt", async () => {
    let disconnected = 0;
    const declined = harness({ disconnectConnector: async () => { disconnected += 1; return { removed: true }; } },
      { confirm: async () => false });
    await declined.actions.connector("disconnect", github);
    expect(disconnected).toBe(0);

    const { state, actions } = harness({ disconnectConnector: async () => ({ removed: true, calls_blocked: true,
      revocation_scope: "current_process", credential_cleanup_confirmed: true, cleanup_status: "confirmed" }) });
    await actions.connector("disconnect", github);
    expect(state.confirms[0]).toContain("断开此连接器");
    expect(state.refreshed).toBe(1);
    expect(state.banners[0]).toContain("本运行实例已禁用");
  });

  it("says nothing about a disconnect that finished after you switched machines", async () => {
    const slow = deferred<{ removed: boolean }>();
    let sent = false;
    const { state, actions } = harness({ disconnectConnector: () => { sent = true; return slow.promise; } });
    const pending = actions.connector("disconnect", github);
    for (let tick = 0; tick < 20 && !sent; tick += 1) await Promise.resolve();
    expect(sent).toBe(true);                      // 请求确实发出去了，才换电脑
    state.scope = "host-b";
    slow.resolve({ removed: true });
    await pending;
    expect(state.banners).toEqual([]);
    expect(state.refreshed).toBe(0);
  });
});

describe("memories", () => {
  it("approving a proposal removes it and reloads memories; a switch in between applies nothing", async () => {
    const { state, actions } = harness({ decideMemoryProposal: async () => undefined,
      listAssistantMemories: async () => [{ id: "m2", content: "住杭州" }] as AssistantMemory[] });
    await actions.decideProposal({ id: "p1" } as MemoryProposal, true);
    expect(state.proposals.map((p) => p.id)).toEqual(["p2"]);
    expect(state.memories.map((m) => m.id)).toEqual(["m2"]);

    const slow = deferred<void>();
    const switched = harness({ decideMemoryProposal: () => slow.promise });
    const pending = switched.actions.decideProposal({ id: "p1" } as MemoryProposal, false);
    switched.state.client = {} as GatewayClient;
    slow.resolve();
    await pending;
    expect(switched.state.proposals).toHaveLength(2);
  });

  it("forgets a memory only after you confirm, and reports failures on the same machine", async () => {
    let deleted = 0;
    const declined = harness({ deleteAssistantMemory: async () => { deleted += 1; } }, { confirm: async () => false });
    await declined.actions.deleteMemory({ id: "m1", content: "住北京" } as AssistantMemory);
    expect(deleted).toBe(0);
    const failing = harness({ deleteAssistantMemory: async () => { throw new Error("网络断了"); } });
    await failing.actions.deleteMemory({ id: "m1", content: "住北京" } as AssistantMemory);
    expect(failing.state.confirms[0]).toContain("住北京");
    expect(failing.state.memories).toHaveLength(1);
    expect(failing.state.banners[0]).toContain("网络断了");
  });
});
