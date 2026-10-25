import { expect, it, vi } from "vitest";
import type { GatewayClient } from "../gateway";
import type { PrDeliveryCheck, PrDeliveryCheckLog, PrDeliverySnapshot } from "../types";
import { prDeliveryActions } from "./prDeliveryActions";

const check: PrDeliveryCheck = { id: "check-1", name: "test", failing: true, pending: false };
const snapshot = (branch = "feature", pr = 1): PrDeliverySnapshot => ({ ok: true, branch, pr,
  comments: [], checks: [check], failing_checks: [check], summary: { total: 1, failed: 1, passed: 0, pending: 0 } });
const log: PrDeliveryCheckLog = { ok: true, check_id: check.id };
const deferred = <T,>() => {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
function client() { return { getPrDelivery: vi.fn(async () => snapshot()), getPrCheckLog: vi.fn(async () => log) }; }
function harness() {
  const owner = client();
  const state = { client: owner as Pick<GatewayClient, "getPrDelivery" | "getPrCheckLog"> | null,
    snapshot: snapshot() as PrDeliverySnapshot | null, logs: {} as Record<string, PrDeliveryCheckLog>, loading: false, banners: [] as string[] };
  const actions = prDeliveryActions({ client: () => state.client, snapshot: () => state.snapshot,
    setSnapshot: value => { state.snapshot = value; }, setLoading: value => { state.loading = value; },
    setLogs: value => { state.logs = typeof value === "function" ? value(state.logs) : value; },
    banner: text => state.banners.push(text) });
  return { state, actions, owner };
}

it("an old refresh cannot replace a newer snapshot or clear its loading indicator", async () => {
  const h = harness(), a = deferred<PrDeliverySnapshot>(), b = deferred<PrDeliverySnapshot>();
  h.owner.getPrDelivery.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
  const first = h.actions.refreshPrDelivery(), second = h.actions.refreshPrDelivery();
  a.resolve(snapshot("old")); await first;
  expect(h.state.loading).toBe(true);
  expect(h.state.snapshot?.branch).toBe("feature");
  b.resolve(snapshot("new")); await second;
  expect(h.state.snapshot?.branch).toBe("new");
  expect(h.state.loading).toBe(false);
});

it.each(["success", "error"])("ignores late %s after a project switch", async outcome => {
  const h = harness(), slow = deferred<PrDeliverySnapshot>();
  h.owner.getPrDelivery.mockReturnValueOnce(slow.promise);
  const old = h.actions.refreshPrDelivery();
  h.state.client = client(); h.actions.reset();
  await h.actions.refreshPrDelivery();
  const current = h.state.snapshot;
  if (outcome === "success") slow.resolve(snapshot("old")); else slow.reject(new Error("old error"));
  await old;
  expect(h.state.snapshot).toBe(current);
  expect(h.state.loading).toBe(false);
});

it("reset invalidates pending results with the same client", async () => {
  const h = harness(), slow = deferred<PrDeliverySnapshot>();
  h.owner.getPrDelivery.mockReturnValueOnce(slow.promise);
  const old = h.actions.refreshPrDelivery(); h.actions.reset();
  slow.resolve(snapshot()); await old;
  expect(h.state.snapshot).toBeNull();
  expect(h.state.loading).toBe(false);
});

it("rejects a delayed CI log after the PR changes, even if the check ID is reused", async () => {
  const h = harness(), slow = deferred<PrDeliveryCheckLog>();
  h.owner.getPrCheckLog.mockReturnValueOnce(slow.promise);
  const old = h.actions.loadPrCheckLog(check);
  h.state.snapshot = snapshot("another", 2);
  slow.resolve(log);
  expect(await old).toBeNull(); expect(h.state.logs).toEqual({});
});

it("does not show a CI log for a check that no longer fails", async () => {
  const h = harness(), slow = deferred<PrDeliveryCheckLog>();
  h.owner.getPrCheckLog.mockReturnValueOnce(slow.promise);
  const old = h.actions.loadPrCheckLog(check);
  h.state.snapshot = { ...snapshot(), failing_checks: [] };
  slow.resolve(log);
  expect(await old).toBeNull(); expect(h.state.logs).toEqual({});
});

it("clears prior PR logs on identity change and keeps a current check's log", async () => {
  const h = harness(); h.state.logs[check.id] = log;
  h.owner.getPrDelivery.mockResolvedValueOnce(snapshot("another", 2));
  await h.actions.refreshPrDelivery();
  expect(h.state.logs).toEqual({});
  expect(await h.actions.loadPrCheckLog(check)).toEqual(log);
  expect(h.state.logs[check.id]).toEqual(log);
});
