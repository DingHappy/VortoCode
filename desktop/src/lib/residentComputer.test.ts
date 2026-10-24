import { expect, it } from "vitest";
import { readResidentComputer, residentComputerReads, type ComputerRead } from "./residentComputer";

export const computer = { schema_version: 1, execution_location: "runtime_host", state: "ready", control_mode: "human",
  viewers: 0, password_store: "unlocked", resident_worker: { connected: false, unattended: false } };

it("keeps runtime-only metadata and never turns readiness into execution permission", () => {
  const result = readResidentComputer({ ...computer, images: ["private.png"], lease: "secret" });
  expect(JSON.stringify(result)).not.toContain("private.png");
  expect(JSON.stringify(result)).not.toContain("secret");
  expect(result?.resident_worker.unattended).toBe(false);
  for (const patch of [{ state: "future" }, { schema_version: 2 }, { execution_location: "controller" },
    { viewers: true }, { viewers: -1 }, { resident_worker: { connected: true, unattended: true } }]) {
    expect(() => readResidentComputer({ ...computer, ...patch })).toThrow();
  }
  expect(readResidentComputer(undefined)).toBeNull();
});

it("keeps observer counts separate and older services unknown", () => {
  expect(readResidentComputer(computer)?.observation).toBeNull();
  const value = readResidentComputer({ ...computer, viewers: 1, observation: { available: true, viewers: 3, secret: "ignore" } });
  expect(value?.viewers).toBe(1);
  expect(value?.observation).toEqual({ available: true, viewers: 3 });
  expect(readResidentComputer({ ...computer, observation: { available: true, viewers: -1 } })?.observation).toBeNull();
});

it("coalesces reads and discards late results from an old runtime", async () => {
  let current = true, resolve!: (value: unknown) => void, calls = 0;
  const published: ComputerRead[] = [];
  const reads = residentComputerReads({ current: () => current, publish: value => published.push(value),
    read: () => { calls++; return new Promise(r => { resolve = r; }); } });
  const first = reads.refresh(), second = reads.refresh();
  expect(first).toBe(second);
  await Promise.resolve();
  current = false; resolve(computer); await first;
  expect(calls).toBe(1);
  expect(published.map(item => item.status)).toEqual(["loading"]);
});

it("preserves old data as unavailable after failure instead of claiming a healthy computer", async () => {
  const published: ComputerRead[] = [];
  let fails = false;
  const reads = residentComputerReads({ current: () => true, now: () => 123,
    publish: value => published.push(value), read: async () => { if (fails) throw Error(); return computer; } });
  await reads.refresh(); fails = true; await reads.refresh();
  const last = published[published.length - 1];
  expect(last.status).toBe("unavailable");
  expect(last.checkedAt).toBe(123);
  expect(last.data?.state).toBe("ready");
});
