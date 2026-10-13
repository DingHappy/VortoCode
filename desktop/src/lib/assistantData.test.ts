import { expect, it, vi } from "vitest";
import type { GatewayClient } from "../gateway";
import { loadAssistantData } from "./assistantData";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
}

it("discards every late response after switching host, including profile and phone history", async () => {
  const slow = deferred<any>();
  const client = { listAgentActivity: () => slow.promise, listDispatches: () => slow.promise,
    getAssistantProfile: () => slow.promise, listPhoneChats: () => slow.promise } as unknown as GatewayClient;
  const sinks = { turns: vi.fn(), dispatches: vi.fn(), profile: vi.fn(), chats: vi.fn() };
  let current = true;
  const loading = loadAssistantData(client, () => current, sinks);
  current = false;
  slow.resolve([]);
  await loading;
  for (const sink of Object.values(sinks)) expect(sink).not.toHaveBeenCalled();
});

it("publishes available data independently and does not wipe a previous snapshot on request failure", async () => {
  const client = { listAgentActivity: async () => [{ title: "activity" }], listDispatches: async () => { throw new Error("offline"); },
    getAssistantProfile: async () => ({ name: "current host" }), listPhoneChats: async () => [] } as unknown as GatewayClient;
  const sinks = { turns: vi.fn(), dispatches: vi.fn(), profile: vi.fn(), chats: vi.fn() };
  await loadAssistantData(client, () => true, sinks);
  expect(sinks.turns).toHaveBeenCalledWith([{ title: "activity" }]);
  expect(sinks.profile).toHaveBeenCalledWith({ name: "current host" });
  expect(sinks.dispatches).not.toHaveBeenCalled();
});

it("a newer refresh wins over an older refresh on the same host", async () => {
  const slow = deferred<any>();
  let epoch = 1;
  const sinks = { turns: vi.fn(), dispatches: vi.fn(), profile: vi.fn(), chats: vi.fn() };
  const client = { listAgentActivity: () => slow.promise, listDispatches: () => slow.promise,
    getAssistantProfile: () => slow.promise, listPhoneChats: () => slow.promise } as unknown as GatewayClient;
  const old = loadAssistantData(client, () => epoch === 1, sinks);
  epoch = 2;
  const fast = { listAgentActivity: async () => ["new"], listDispatches: async () => [],
    getAssistantProfile: async () => ({ name: "new" }), listPhoneChats: async () => [] } as unknown as GatewayClient;
  await loadAssistantData(fast, () => epoch === 2, sinks);
  slow.resolve(["old"]); await old;
  expect(sinks.turns).toHaveBeenCalledTimes(1);
  expect(sinks.turns).toHaveBeenLastCalledWith(["new"]);
});

it("manual checks refresh only tasks and surface errors to the card", async () => {
  const client = { listAgentActivity: vi.fn(), listDispatches: vi.fn(async () => { throw new Error("核对失败"); }),
    getAssistantProfile: vi.fn(), listPhoneChats: vi.fn() } as unknown as GatewayClient;
  const sinks = { turns: vi.fn(), dispatches: vi.fn(), dispatchError: vi.fn(), profile: vi.fn(), chats: vi.fn() };
  await expect(loadAssistantData(client, () => true, sinks, "dsp-a")).rejects.toThrow("核对失败");
  expect(client.listDispatches).toHaveBeenCalledWith("dsp-a");
  expect(sinks.dispatchError).toHaveBeenCalledTimes(1);
  expect(client.listAgentActivity).not.toHaveBeenCalled();
  expect(client.getAssistantProfile).not.toHaveBeenCalled();
  expect(client.listPhoneChats).not.toHaveBeenCalled();
});

it("a profile read started before saving cannot restore the previous identity", async () => {
  const profile = deferred<any>(); let revision = 1;
  const client = { listAgentActivity: async () => [], listDispatches: async () => [],
    getAssistantProfile: () => profile.promise, listPhoneChats: async () => [] } as unknown as GatewayClient;
  const sinks = { turns: vi.fn(), dispatches: vi.fn(), profile: vi.fn(), chats: vi.fn() };
  const reading = loadAssistantData(client, () => revision === 1, sinks);
  revision = 2; sinks.profile({ name: "saved name" });
  profile.resolve({ name: "old name" }); await reading;
  expect(sinks.profile).toHaveBeenCalledTimes(1);
  expect(sinks.profile).toHaveBeenLastCalledWith({ name: "saved name" });
});

it("avatar: late response after switching host is dropped; a server without the route keeps the initial", async () => {
  const slow = deferred<any>();
  const base = { listAgentActivity: async () => [], listDispatches: async () => [],
    getAssistantProfile: async () => ({ name: "x" }), listPhoneChats: async () => [] };
  const late = { ...base, getAssistantAvatar: () => slow.promise } as unknown as GatewayClient;
  const avatar = vi.fn();
  let current = true;
  const loading = loadAssistantData(late, () => current, { turns: vi.fn(), dispatches: vi.fn(), profile: vi.fn(), chats: vi.fn(), avatar });
  current = false;
  slow.resolve({ type: "image/png", data: "AAAA" });
  await loading;
  expect(avatar).not.toHaveBeenCalled();

  const old = { ...base, getAssistantAvatar: async () => { throw new Error("404"); } } as unknown as GatewayClient;
  const dispatches = vi.fn();
  await expect(loadAssistantData(old, () => true, { turns: vi.fn(), dispatches, profile: vi.fn(), chats: vi.fn(), avatar })).resolves.toBeUndefined();
  expect(avatar).not.toHaveBeenCalled();
  expect(dispatches).toHaveBeenCalled();
});
