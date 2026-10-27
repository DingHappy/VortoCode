import assert from "node:assert/strict";
import { test } from "node:test";
import { createPairingSession } from "../src/pairing.ts";
import { MobileClientError } from "../src/index.ts";

const instance = "a".repeat(32);
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
function gateway() {
  let approved = false, revoked = false, wrongInstance = false, requestId = "", credential = "";
  const starts: string[] = [], paths: string[] = [];
  const transport: typeof fetch = async (url, init) => {
    const path = new URL(String(url)).pathname; paths.push(path);
    assert.equal(new URL(String(url)).search, "");
    assert.equal(init?.credentials, "omit"); assert.equal(init?.redirect, "error"); assert.equal(init?.cache, "no-store");
    if (path.includes("/pairing/")) {
      assert.equal(init?.method, "POST"); assert.equal(new Headers(init?.headers).has("Authorization"), false);
      const body = JSON.parse(String(init?.body));
      if (path.endsWith("start")) {
        starts.push(String(init?.body)); requestId = body.request_id; credential = body.device_token;
        assert.match(credential, /^vcm_[A-Za-z0-9_-]{43}$/);
      } else { assert.equal(body.device_token, credential); assert.equal(body.request_id, requestId); }
      if (path.endsWith("cancel")) revoked = true;
      const device = { id: requestId, name: "手机", created_at: 100, expires_at: 900, revoked_at: null, scopes: ["assistant:read"] };
      return json({ schema_version: 1, instance_id: wrongInstance ? "b".repeat(32) : instance, id: requestId,
        name: "手机", code: "ABCD-EFGH", expires_at: 400, scopes: ["assistant:read"],
        status: revoked ? "revoked" : approved ? "approved" : "pending", device: approved && !revoked ? device : null });
    }
    assert.equal(path, "/api/mobile/v1/bootstrap"); assert.equal(new Headers(init?.headers).get("Authorization"), `Bearer ${credential}`);
    return json({ schema_version: 1, instance_id: instance,
      device: { id: requestId, name: "手机", created_at: 100, expires_at: 900, revoked_at: null, scopes: ["assistant:read"] },
      capabilities: { reads: ["profile"], writes: [] } });
  };
  return { transport, starts, paths, approve: () => { approved = true; }, changeInstance: () => { wrongInstance = true; } };
}
test("phone retains one request identity and proof across response retries; server approval is required", async () => {
  const server = gateway(), session = createPairingSession({ origin: "https://gateway.test", fetch: server.transport }, "手机");
  const pending = await session.start(); await session.start();
  assert.equal(pending.status, "pending"); assert.equal(server.starts[0], server.starts[1]);
  assert.equal("token" in pending, false);
  await assert.rejects(session.connect(), error => error instanceof MobileClientError && error.code === "unauthorized");
  assert.equal(server.paths.includes("/api/mobile/v1/bootstrap"), false);
  server.approve(); assert.equal((await session.status()).status, "approved");
  const client = await session.connect(); assert.deepEqual(Object.keys(client).sort(), ["bootstrap", "snapshot"]);
  await session.cancel();
  await assert.rejects(session.connect(), error => error instanceof MobileClientError && error.code === "unauthorized");
});
test("pairing status cannot silently change server identity", async () => {
  const server = gateway(), session = createPairingSession({ origin: "https://gateway.test", fetch: server.transport }, "手机");
  await session.start(); server.changeInstance();
  await assert.rejects(session.status(), error => error instanceof MobileClientError && error.code === "wrong_instance");
});
test("two phone sessions never share their credential or request identity", async () => {
  const server = gateway(), options = { origin: "https://gateway.test", fetch: server.transport };
  await createPairingSession(options, "A").start(); await createPairingSession(options, "B").start();
  const first = JSON.parse(server.starts[0]), second = JSON.parse(server.starts[1]);
  assert.notEqual(first.request_id, second.request_id); assert.notEqual(first.device_token, second.device_token);
});
