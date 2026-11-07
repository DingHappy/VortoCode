import { afterEach, expect, it, vi } from "vitest";
import { createResourceReads, parseSessionResources, type ResourceView, type ResourceSource, type ResourceClient } from "./sessionResources";

const snapshot = (nonce = "n1", session = "sid-one") => ({ version: 1, session, nonce,
  observed_at: "2026-11-01T12:00:00Z", status: "observed", truncated: false,
  items: [{ id: "browser", kind: "browser", title: "网站浏览器", state: "needs_human", approval: "each_action",
    target: "https://example.com", account: "工作", device: "", detail: "等待真人", checked_at: null }] });

afterEach(() => vi.useRealTimers());
it("projects only known public fields", () => {
  const raw = snapshot();
  expect(parseSessionResources({ ...raw, secret: "hidden", items: [{ ...raw.items[0], cookie: "hidden" }] })).toEqual(raw);
});

it("accepts bounded declaration metadata without coercing write tools into read-only tools", () => {
  const tool_inventory = { version: 1, truncated: false, items: [{ name: "edit_file", read_only: false, required_capabilities: ["write_repo"], secret: "hidden" }] };
  const view = parseSessionResources({ ...snapshot(), tool_inventory });
  expect(view?.tool_inventory?.items).toEqual([{ name: "edit_file", read_only: false, required_capabilities: ["write_repo"] }]);
  for (const invalid of [{ ...tool_inventory, version: 2 }, { ...tool_inventory, items: [{ ...tool_inventory.items[0], read_only: "false" }] },
    { ...tool_inventory, items: Array(257).fill(tool_inventory.items[0]) }, { ...tool_inventory, items: [tool_inventory.items[0], tool_inventory.items[0]] }]) {
    const compatibleResources = parseSessionResources({ ...snapshot(), tool_inventory: invalid });
    expect(compatibleResources?.items).toHaveLength(1);
    expect(compatibleResources?.tool_inventory).toBeUndefined();
  }
  expect(parseSessionResources(snapshot())?.tool_inventory).toBeUndefined();
});
it.each([null, {}, { ...snapshot(), version: 2 }, { ...snapshot(), observed_at: "bad" },
  { ...snapshot(), status: "ready" }, { ...snapshot(), truncated: "false" },
  { ...snapshot(), items: [...snapshot().items, ...snapshot().items] },
  { ...snapshot(), items: Array(33).fill(snapshot().items[0]) },
  { ...snapshot(), status: "not_loaded" }, { ...snapshot(), items: [{ ...snapshot().items[0], state: "authorized" }] },
  { ...snapshot(), items: [{ ...snapshot().items[0], account: "secret\nvalue" }] }])("rejects malformed snapshots %j", value => {
    expect(parseSessionResources(value)).toBeNull();
  });

function fixture() {
  vi.useFakeTimers();
  const sends: Record<string, unknown>[] = [], views: ResourceView[] = [];
  let source: ResourceSource<ResourceClient> = { client: { send: vi.fn(async (event: Record<string, unknown>) => { sends.push(event); }) }, session: "one", connected: true };
  let sequence = 0;
  const reader = createResourceReads(() => source, (_source, view) => views.push(view), () => `n${++sequence}`);
  reader.reset();
  return { reader, sends, views, source: () => source, set: (change: Partial<typeof source>) => { source = { ...source, ...change }; },
    current: () => views[views.length - 1]! };
}
it("sends only one read request and ignores uncorrelated replies", async () => {
  const f = fixture(); await f.reader.load(); await f.reader.load();
  expect(f.sends).toEqual([{ type: "get_status", resources: "n1" }]);
  f.reader.accept(snapshot("old")); expect(f.current().busy).toBe(true);
  f.reader.accept(snapshot()); expect(f.current().snapshot?.items[0].state).toBe("needs_human");
  expect(f.current().stale).toBe(false); f.reader.dispose();
});
it("times out unsupported servers without polling or retry, then permits explicit refresh", async () => {
  const f = fixture(); await f.reader.load(); f.reader.accept(undefined);
  await vi.advanceTimersByTimeAsync(5000);
  expect(f.current().busy).toBe(false); expect(f.current().stale).toBe(true);
  expect(f.sends).toHaveLength(1); await vi.advanceTimersByTimeAsync(90_000); expect(f.sends).toHaveLength(1);
  await f.reader.load(); expect(f.sends).toHaveLength(2); f.reader.dispose();
});
it.each(["session", "client", "epoch"])("does not adopt late observations across %s changes", async kind => {
  const f = fixture(); await f.reader.load();
  if (kind === "session") f.set({ session: "two" }); else if (kind === "epoch") f.set({ epoch: 1 }); else f.set({ client: { send: async event => { f.sends.push(event); } } });
  await f.reader.load(); expect(f.current().snapshot).toBeNull();
  f.reader.accept(snapshot()); expect(f.current().busy).toBe(true);
  f.reader.accept(snapshot("n2", kind === "session" ? "sid-two" : "sid-one"));
  expect(f.current().busy).toBe(false); f.reader.dispose();
});
it("retains an old snapshot as stale on disconnect and send failure", async () => {
  const f = fixture(); await f.reader.load(); f.reader.accept(snapshot());
  f.set({ connected: false }); f.reader.offline(); await f.reader.load();
  expect(f.sends).toHaveLength(1); expect(f.current().snapshot).not.toBeNull(); expect(f.current().stale).toBe(true);
  f.set({ connected: true }); f.source().client!.send = vi.fn(async () => { throw Error("private error"); });
  await f.reader.load(); expect(f.current().error).not.toContain("private"); expect(f.current().snapshot).not.toBeNull(); f.reader.dispose();
});
it("marks observations stale on new activity and expires locally without a request", async () => {
  const f = fixture(); await f.reader.load(); f.reader.changed(); f.reader.accept(snapshot());
  expect(f.current().stale).toBe(true);
  await f.reader.load(); f.reader.accept(snapshot("n2")); expect(f.current().stale).toBe(false);
  await vi.advanceTimersByTimeAsync(60_000); expect(f.current().stale).toBe(true); expect(f.sends).toHaveLength(2); f.reader.dispose();
});
it("rejects a matching nonce from another session and malformed payloads", async () => {
  const f = fixture(); await f.reader.load(); f.reader.accept(snapshot("n1", "sid-other"));
  expect(f.current().snapshot).toBeNull(); expect(f.current().error).toContain("归属");
  await f.reader.load(); f.reader.accept({ nonce: "n2", version: 99 }); expect(f.current().busy).toBe(false); f.reader.dispose();
});
