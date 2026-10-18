import { describe, expect, it } from "vitest";

import { AUTO_CHECK_INTERVAL_MS, autoCheckDue } from "./updateCheck";

describe("autoCheckDue", () => {
  const now = 1_800_000_000_000;
  it("checks at most once a day", () => {
    expect(autoCheckDue(null, now)).toBe(true);
    expect(autoCheckDue(now - 1000, now)).toBe(false);
    expect(autoCheckDue(now - AUTO_CHECK_INTERVAL_MS, now)).toBe(true);
  });
  it("recovers from bad records and clocks moved backwards", () => {
    expect(autoCheckDue(Number.NaN, now)).toBe(true);
    expect(autoCheckDue(now + 60_000, now)).toBe(true);
  });
});
