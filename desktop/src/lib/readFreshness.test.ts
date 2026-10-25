import { expect, it } from "vitest";
import { freshAt, SOURCE_FRESH_MS } from "./readFreshness";

it("uses the same two-minute read window for workday and goal details", () => {
  expect(SOURCE_FRESH_MS).toBe(120_000);
  expect(freshAt(1_000, 1_000)).toBe(true);
  expect(freshAt(1_000, 121_000)).toBe(true);
  expect(freshAt(1_000, 121_001)).toBe(false);
  expect(freshAt(121_001, 121_001)).toBe(true);
});

it.each([undefined, NaN, Infinity, -Infinity, 101])("does not trust missing, invalid or future read time %s", checkedAt => {
  expect(freshAt(checkedAt, 100)).toBe(false);
});

it.each([NaN, Infinity, -Infinity, 99])("requires a valid current clock at or after the read: %s", now => {
  expect(freshAt(100, now)).toBe(false);
});

it("retains the separate thirty-minute Codex report boundary", () => {
  expect(freshAt(1_000, 1_801_000, 30 * 60_000)).toBe(true);
  expect(freshAt(1_000, 1_801_001, 30 * 60_000)).toBe(false);
});
