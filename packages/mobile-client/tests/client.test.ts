import assert from "node:assert/strict";
import { test } from "node:test";
import { createMobileClient, createMobileReader, MobileClientError, readBootstrap, readSnapshot } from "../src/index.ts";
import type { Snapshot } from "../src/index.ts";

const instance = "a".repeat(32), token = "vcm_" + "x".repeat(43);
const observation = <T>(data: T) => ({ status: "current", checked_at: "2026-10-25T15:00:00+00:00", data });
const unavailable = () => ({ status: "unavailable", checked_at: null, data: null });
const wire = () => ({ schema_version: 1, instance_id: instance, sources: {
  profile: observation({ name: "VortoCode" }), main_chat: observation({ sid: "main" }),
  goals: observation({ items: [{ id: "personal-1", objective: "核对", status: "active", next_action: "查看依据", blocker: "", revision: 1 }], truncated: false }),
} });
const boot = () => ({ schema_version: 1, instance_id: instance,
  device: { id: "b".repeat(32), name: "手机", created_at: 100, expires_at: 200, revoked_at: null, scopes: ["assistant:read"] },
  capabilities: { reads: ["profile", "main_chat", "goals"], writes: [] },
});
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const hasCode = (code: string) => (error: unknown) => error instanceof MobileClientError && error.code === code;
const deferred = <T>() => {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test("only two fixed read paths, no cookies, redirects or credential in URL", async () => {
  const seen: string[] = [];
  const client = createMobileClient({ origin: "https://gateway.test", instanceId: instance, credential: () => token,
    fetch: async (url, init) => {
      seen.push(String(url)); assert.equal(init?.method, "GET"); assert.equal(init?.credentials, "omit");
      assert.equal(init?.redirect, "error"); assert.equal(init?.cache, "no-store");
      assert.equal(new Headers(init?.headers).get("Authorization"), `Bearer ${token}`);
      return json(String(url).endsWith("bootstrap") ? boot() : wire());
    },
  });
  await client.bootstrap(); await client.snapshot();
  assert.deepEqual(seen, ["https://gateway.test/api/mobile/v1/bootstrap", "https://gateway.test/api/mobile/v1/snapshot"]);
  assert.deepEqual(Object.keys(client).sort(), ["bootstrap", "snapshot"]);
});

test("unknown fields are additive, unknown writes never become methods or authority", () => {
  const data = boot(); data.capabilities.writes = ["approve"] as never[];
  assert.deepEqual(readBootstrap({ ...data, future: true }, instance).capabilities.writes, []);
  const snapshot = readSnapshot(wire(), instance);
  assert.equal(snapshot.sources.goals.data?.items[0].status, "active");
});

test("reject version drift, wrong instance, malformed sources and missing capability scope", () => {
  assert.throws(() => readSnapshot({ ...wire(), schema_version: 2 }, instance), hasCode("protocol"));
  assert.throws(() => readSnapshot(wire(), "c".repeat(32)), hasCode("wrong_instance"));
  const duplicate = wire(); duplicate.sources.goals.data.items.push(duplicate.sources.goals.data.items[0]);
  assert.throws(() => readSnapshot(duplicate, instance), hasCode("protocol"));
  const malformed = wire(); malformed.sources.main_chat.data.sid = "../other";
  assert.throws(() => readSnapshot(malformed, instance), hasCode("protocol"));
  assert.throws(() => readSnapshot({ ...wire(), sources: { ...wire().sources, goals: { ...unavailable(), data: { items: [] } } } }, instance), hasCode("protocol"));
  const noScope = boot(); noScope.device.scopes = [];
  assert.throws(() => readBootstrap(noScope, instance), hasCode("protocol"));
});

test("insecure transport only on explicitly enabled loopback; no credentials/path/query in endpoint", () => {
  for (const origin of ["http://gateway.test", "http://localhost", "https://user:secret@gateway.test", "https://gateway.test/path", "https://gateway.test/?token=secret", "https://gateway.test/#secret", "file:///tmp/test"]) {
    assert.throws(() => createMobileClient({ origin, instanceId: instance, credential: () => token }), hasCode("configuration"));
  }
  createMobileClient({ origin: "http://127.0.0.1:8000", instanceId: instance, credential: () => token, allowInsecureLoopback: true });
  assert.throws(() => createMobileClient({ origin: "http://192.168.1.97:8000", instanceId: instance, credential: () => token, allowInsecureLoopback: true }), hasCode("configuration"));
});

test("unauthorized, non-JSON and network errors are classified without response/secret leakage or retry", async () => {
  for (const [response, expected] of [[json({ detail: token }, 401), "unauthorized"], [json({}, 503), "unavailable"], [new Response("login " + token), "protocol"]] as const) {
    let calls = 0;
    const client = createMobileClient({ origin: "https://gateway.test", instanceId: instance, credential: () => token,
      fetch: async () => { calls++; return response; } });
    await assert.rejects(client.snapshot(), error => hasCode(expected)(error) && !String(error).includes(token));
    assert.equal(calls, 1);
  }
  const client = createMobileClient({ origin: "https://gateway.test", instanceId: instance, credential: () => token,
    fetch: async () => { throw new Error(token); } });
  await assert.rejects(client.snapshot(), error => hasCode("unavailable")(error) && !String(error).includes(token));
});

test("credential change while reading discards the late response", async () => {
  const result = deferred<Response>(); let credential = token;
  const client = createMobileClient({ origin: "https://gateway.test", instanceId: instance, credential: () => credential,
    fetch: () => result.promise });
  const pending = client.snapshot(); credential = "vcm_" + "y".repeat(43); result.resolve(json(wire()));
  await assert.rejects(pending, hasCode("credential_changed"));
});

test("secure-storage provider failures do not leak secret errors", async () => {
  const client = createMobileClient({ origin: "https://gateway.test", instanceId: instance,
    credential: () => { throw new Error(token); } });
  await assert.rejects(client.snapshot(), error => hasCode("unauthorized")(error) && !String(error).includes(token));
});

test("aborted and timed out reads cannot accept a late successful response", async () => {
  const result = deferred<Response>(), controller = new AbortController();
  const client = createMobileClient({ origin: "https://gateway.test", instanceId: instance, credential: () => token,
    fetch: () => result.promise });
  const pending = client.snapshot(controller.signal); controller.abort(); result.resolve(json(wire()));
  await assert.rejects(pending, hasCode("aborted"));
  const timeoutClient = createMobileClient({ origin: "https://gateway.test", instanceId: instance, credential: () => token, timeoutMs: 2,
    fetch: (_url, init) => new Promise((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(new Error("aborted")))) });
  await assert.rejects(timeoutClient.snapshot(), hasCode("timeout"));
});

test("new refresh owns the result even when old transport ignores cancellation", async () => {
  const first = deferred<Snapshot>(), second = deferred<Snapshot>(), states: string[] = [];
  let calls = 0;
  const reader = createMobileReader({ snapshot: () => (++calls === 1 ? first.promise : second.promise) }, state => states.push(state.snapshot?.sources.profile.data?.name ?? state.status));
  const oldRead = reader.refresh(), newRead = reader.refresh();
  const newest = readSnapshot(wire(), instance); newest.sources.profile.data!.name = "newest";
  second.resolve(newest); await newRead;
  first.resolve(readSnapshot(wire(), instance)); await oldRead;
  assert.equal(states.at(-1), "newest");
  const third = deferred<Snapshot>();
  const disconnected: string[] = [];
  const another = createMobileReader({ snapshot: () => third.promise }, state => disconnected.push(state.status));
  const pending = another.refresh(); another.disconnect(); third.resolve(readSnapshot(wire(), instance)); await pending;
  assert.deepEqual(disconnected, ["loading", "idle"]);
});

test("partial failures preserve historical observations, auth errors erase the connection view", async () => {
  const states: Parameters<Parameters<typeof createMobileReader>[1]>[0][] = [];
  let calls = 0;
  const reader = createMobileReader({ snapshot: async () => {
    if (++calls === 1) return readSnapshot(wire(), instance);
    if (calls === 2) return readSnapshot({ ...wire(), sources: { ...wire().sources, goals: unavailable() } }, instance);
    if (calls === 3) throw new MobileClientError("unavailable");
    throw new MobileClientError("unauthorized");
  } }, state => states.push(state));
  await reader.refresh(); await reader.refresh();
  const goals = states.at(-1)!.snapshot!.sources.goals;
  assert.equal(goals.status, "unavailable"); assert.equal(goals.data!.items[0].id, "personal-1");
  assert.equal(goals.checked_at, "2026-10-25T15:00:00+00:00");
  await reader.refresh();
  assert.equal(states.at(-1)!.snapshot!.sources.profile.status, "unavailable");
  await reader.refresh(); assert.equal(states.at(-1)!.snapshot, null);
});
