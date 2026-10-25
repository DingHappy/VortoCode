import { describe, expect, it } from "vitest";

import type { GatewayClient } from "../gateway";
import type { AssistantPlugin, PluginCheck } from "../types";
import { pluginActions, refreshPlugins, type PluginActionDeps, type PluginChecks, type PluginSnapshot } from "./pluginActions";

const plugin = { name: "calendar", title: "日历" } as AssistantPlugin;
const ok = { ok: true } as unknown as PluginCheck;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

/** 假前台：client 和 scope 都能在请求途中被换掉。 */
function harness(client: Partial<GatewayClient>, confirm = true) {
  const state = { client: client as GatewayClient | null, scope: "host-a", banners: [] as string[],
    plugins: null as PluginSnapshot | null, checks: null as PluginChecks | null, busy: [] as string[], confirms: 0 };
  const deps: PluginActionDeps = {
    client: () => state.client,
    scope: () => state.scope,
    renderScope: "host-a",
    confirm: async () => { state.confirms += 1; return confirm; },
    setBanner: (text) => state.banners.push(text),
    setPlugins: (snapshot) => { state.plugins = snapshot; },
    setChecks: (next) => { state.checks = typeof next === "function" ? next(state.checks) : next; },
    setBusy: (name) => state.busy.push(name),
  };
  return { state, deps, actions: pluginActions(deps) };
}

describe("plugin actions", () => {
  it("a read that returns after switching machines is dropped", async () => {
    const slow = deferred<AssistantPlugin[]>();
    const client = { listPlugins: () => slow.promise } as Partial<GatewayClient>;
    const { state, deps } = harness(client);
    const pending = refreshPlugins(deps, client as GatewayClient, "host-a");
    state.scope = "host-b";
    slow.resolve([plugin]);
    await pending;
    expect(state.plugins).toBeNull();
  });

  it("save records the check under the scope it was clicked in and re-reads the list", async () => {
    const { state, actions } = harness({
      savePluginCredential: async () => ({ saved: true, check: ok }),
      listPlugins: async () => [plugin],
    });
    expect(await actions.save(plugin, "token")).toBe(true);
    expect(state.checks).toEqual({ scope: "host-a", items: { calendar: ok } });
    expect(state.plugins).toEqual({ scope: "host-a", items: [plugin] });
    expect(state.busy).toEqual(["calendar", ""]);
  });

  it("a save that fails after switching machines neither banners nor clears the new machine's busy flag", async () => {
    const slow = deferred<{ saved: boolean; check: PluginCheck }>();
    const { state, actions } = harness({ savePluginCredential: () => slow.promise });
    const pending = actions.save(plugin, "token");
    state.scope = "host-b";
    slow.reject(new Error("boom"));
    expect(await pending).toBe(false);
    expect(state.banners).toEqual([]);
    expect(state.busy).toEqual(["calendar"]);
  });

  it("remove asks first, and declining touches nothing", async () => {
    let removed = 0;
    const { state, actions } = harness({ removePluginCredential: async () => { removed += 1; return { removed: true }; } }, false);
    await actions.act("remove", plugin);
    expect(state.confirms).toBe(1);
    expect(removed).toBe(0);
    expect(state.busy).toEqual([]);
  });

  it("remove drops only that plugin's check result and keeps other machines' results out", async () => {
    const failing = { ok: false } as unknown as PluginCheck;
    const { state, actions } = harness({
      removePluginCredential: async () => ({ removed: true }), listPlugins: async () => [],
      checkPlugin: async () => failing,
    });
    state.checks = { scope: "host-a", items: { calendar: ok, notion: ok } };
    await actions.act("remove", plugin);
    expect(state.checks).toEqual({ scope: "host-a", items: { notion: ok } });

    state.checks = { scope: "host-b", items: { calendar: ok } };
    await actions.act("check", plugin);
    expect(state.checks).toEqual({ scope: "host-a", items: { calendar: failing } });
    expect(state.banners).toEqual([]);
  });
});
