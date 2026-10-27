import assert from "node:assert/strict";
import { createInterface } from "node:readline";
import { createPairingSession } from "../src/pairing.ts";
import { MobileClientError } from "../src/index.ts";

const lines = createInterface({ input: process.stdin })[Symbol.asyncIterator]();
const configuration = JSON.parse((await lines.next()).value!);
const session = createPairingSession({ origin: configuration.origin, allowInsecureLoopback: true }, "Contract pairing phone");
const pending = await session.start();
// This public code/ID goes only to the isolated test's trusted admin controller.
console.log(JSON.stringify({ id: pending.id, code: pending.code }));
assert.equal((await lines.next()).value, "approved");
const client = await session.connect();
assert.equal((await client.snapshot()).sources.profile.data?.name, "Smoke assistant");
console.log("read-verified");
assert.equal((await lines.next()).value, "revoked");
await assert.rejects(client.snapshot(), error => error instanceof MobileClientError && error.code === "unauthorized");
console.log("revoked-verified");
process.exit(0);
