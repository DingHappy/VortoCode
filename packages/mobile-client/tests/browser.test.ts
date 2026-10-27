import assert from "node:assert/strict";
import { test } from "node:test";
import { createBrowserClient } from "../src/browser.ts";
import { MobileClientError } from "../src/index.ts";
const id = "a".repeat(32);
const status = { schema_version: 1, ready: true, signed_in: true, instance_id: id, name: "助手", account_origin: "https://account.test", discovery_scope: "current_runtime" };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const hasCode = (code: string) => (error: unknown) => error instanceof MobileClientError && error.code === code;
const boot = { schema_version: 1, instance_id: id, device: { id, name: "手机", created_at: 100, expires_at: 200, revoked_at: null, scopes: ["assistant:read"] }, capabilities: { reads: ["goals"], writes: [] } };
test("BFF uses same-origin cookies and fixed routes without JS credentials", async () => {
  const seen: string[] = [];
  const client = createBrowserClient({ origin: "https://gateway.test", fetch: async (url, init) => {
    seen.push(String(url)); assert.equal(init?.credentials, "same-origin"); assert.equal(init?.cache, "no-store"); assert.equal(init?.redirect, "error");
    assert.equal(new Headers(init?.headers).has("Authorization"), false);
    assert.equal(init?.method, String(url).endsWith("logout") ? "POST" : "GET");
    return json(String(url).endsWith("status") ? status : String(url).endsWith("logout") ? { ok: true } : boot);
  } });
  await assert.rejects(client.bootstrap(), hasCode("unauthorized"));
  await client.status(); await client.bootstrap(); await client.logout();
  await assert.rejects(client.bootstrap(), hasCode("unauthorized"));
  assert.deepEqual(seen.map(url => new URL(url).pathname), ["/api/mobile/browser/status", "/api/mobile/browser/bootstrap", "/api/mobile/browser/logout"]);
});
test("only first-party authorize URL with registered client and callback is accepted", async () => {
  const valid = new URL("https://account.test/authorize"); valid.searchParams.set("client_id", "vortocode-mobile-web"); valid.searchParams.set("redirect_uri", "https://gateway.test/api/mobile/account/callback");
  for (const invalid of ["https://evil.test/authorize", "https://account.test/other", valid.href.replace("mobile-web", "desktop"), valid.href + "#fragment"]) {
    const client = createBrowserClient({ origin: "https://gateway.test", fetch: async url => json(String(url).endsWith("status") ? status : { authorize_url: invalid }) });
    await client.status(); await assert.rejects(client.login(), hasCode("protocol"));
  }
  const client = createBrowserClient({ origin: "https://gateway.test", fetch: async url => json(String(url).endsWith("status") ? status : { authorize_url: valid.href }) });
  await client.status(); assert.equal(await client.login(), valid.href);
});
test("status and disconnect invalidate a late read even if transport ignores abort", async () => {
  let resolve!: (value: Response) => void;
  const client = createBrowserClient({ origin: "https://gateway.test", fetch: async url => String(url).endsWith("status") ? json(status) : new Promise<Response>(yes => { resolve = yes; }) });
  await client.status(); const read = client.bootstrap(); client.disconnect(); resolve(json(boot));
  await assert.rejects(read, hasCode("credential_changed"));
});
test("unready state stays explicit; malformed and cross-instance responses are rejected", async () => {
  for (const value of [{ ...status, schema_version: 2 }, { ...status, instance_id: "wrong" }, { ...status, account_origin: "http://account.test" }, { ...status, discovery_scope: "all" }]) {
    const client = createBrowserClient({ origin: "https://gateway.test", fetch: async () => json(value) });
    await assert.rejects(client.status(), error => error instanceof MobileClientError);
  }
  const client = createBrowserClient({ origin: "https://gateway.test", fetch: async url => json(String(url).endsWith("status") ? status : { ...boot, instance_id: "b".repeat(32) }) });
  await client.status(); await assert.rejects(client.bootstrap(), hasCode("wrong_instance"));
  const unready = createBrowserClient({ origin: "https://gateway.test", fetch: async () => json({ schema_version: 1, ready: false, signed_in: false, message: "请先登记助手" }) });
  assert.equal((await unready.status()).ready, false); await assert.rejects(unready.login(), hasCode("configuration"));
});
test("authorization failures erase client identity and never expose response bodies", async () => {
  const client = createBrowserClient({ origin: "https://gateway.test", fetch: async url => json(String(url).endsWith("status") ? status : { detail: "private-value" }, String(url).endsWith("status") ? 200 : 401) });
  await client.status(); await assert.rejects(client.bootstrap(), error => hasCode("unauthorized")(error) && !String(error).includes("private-value"));
  await assert.rejects(client.snapshot(), hasCode("unauthorized"));
});
