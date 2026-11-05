import { describe, expect, it } from "vitest";
import { parseMemorySnapshot } from "./memorySnapshot";
import { snapshotFixture } from "../test/memorySnapshotFixture";

describe("memory snapshot boundary", () => {
  it("keeps unknown provenance and literal content as background data", () => {
    const snapshot = snapshotFixture();
    expect(parseMemorySnapshot(snapshot)).toEqual(snapshot);
    expect(parseMemorySnapshot({ ...snapshot, total: 2, coverage_limited: true }).coverage_limited).toBe(true);
  });

  it.each([
    { version: 2 }, { revision: "missing" }, { count: 2 }, { total: -1 }, { limit: 1001 },
    { coverage_limited: true }, { items: null }, { items: [null] },
    { items: [{ ...snapshotFixture().items[0], source: { trusted: true } }] },
  ])("rejects incomplete or unsupported snapshots: %j", patch => {
    expect(() => parseMemorySnapshot({ ...snapshotFixture(), ...patch })).toThrow("记忆快照格式不完整");
  });
});
