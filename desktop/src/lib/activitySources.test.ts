import { expect, it } from "vitest";
import { activityCoverage, type ActivitySource } from "./activitySources";
const now = 1_000_000;
const sources: ActivitySource[] = ["turns", "dispatches", "codexActions", "notices"].map(name => ({ name: name as ActivitySource["name"], status: "current", checkedAt: now - 1000 }));

it("only complete fresh reads support a current pending count", () => {
  expect(activityCoverage(sources, true, now)).toMatchObject({ complete: true, checkedAt: now - 1000, issues: [] });
  expect(activityCoverage(sources.map(source => ({ ...source, checking: true })), true, now).complete).toBe(true);
  for (const status of ["loading", "unavailable"] as const) {
    expect(activityCoverage([{ ...sources[0], status }, ...sources.slice(1)], true, now).complete).toBe(false);
  }
  expect(activityCoverage([], true, now).complete).toBe(false);
});
it("expiry, future clocks, missing success times and disconnects cannot establish complete coverage", () => {
  for (const checkedAt of [undefined, NaN, now + 1, now - 120_001]) {
    expect(activityCoverage([{ ...sources[0], checkedAt }], true, now).complete).toBe(false);
  }
  expect(activityCoverage(sources, false, now).complete).toBe(false);
});
