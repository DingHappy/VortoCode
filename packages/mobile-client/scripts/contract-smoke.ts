/** Private stdin only: never put a grant in argv, URL, stdout or test artifacts. */
import assert from "node:assert/strict";
import { createMobileClient, MobileClientError } from "../src/index.ts";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const configuration = JSON.parse(input);
const client = createMobileClient({ origin: configuration.origin, instanceId: configuration.instance_id,
  credential: () => configuration.token, allowInsecureLoopback: true });
if (configuration.revoked) {
  await assert.rejects(client.snapshot(), error => error instanceof MobileClientError && error.code === "unauthorized");
  console.log("Revoked device rejected by the real HTTP route.");
} else {
  const bootstrap = await client.bootstrap(), snapshot = await client.snapshot();
  assert.deepEqual(bootstrap.capabilities.writes, []);
  assert.equal(snapshot.sources.profile.data?.name, "Smoke assistant");
  assert.equal(snapshot.sources.main_chat.data?.sid, "smoke-main");
  assert.equal(snapshot.sources.goals.data?.items.length, 1);
  assert.equal(snapshot.sources.goals.data?.items[0].status, "active");
  console.log("Device bootstrap and three read sources passed real HTTP / TypeScript contract checks.");
}
