import { describe, expect, it } from "vitest";
import type { ArtifactMeta } from "../types";
import { artifactTimeLabel, groupArtifacts } from "./libraryGroups";

const item = (id: string, updated_at: string): ArtifactMeta =>
  ({ id, title: id, kind: "html", version: 1, created_at: updated_at, updated_at, bytes: 1, url: "" });

describe("libraryGroups", () => {
  const now = new Date(2026, 9, 28, 15, 0);

  it("按今天 / 昨天 / 这 7 天 / 更早分组，组内新的在前，读不出时间的归更早", () => {
    const groups = groupArtifacts([
      item("old", "2026-09-01T10:00:00"), item("today-early", "2026-10-28T08:00:00"), item("broken", ""),
      item("yesterday", "2026-10-27T23:59:00"), item("today-late", "2026-10-28T14:00:00"), item("week", "2026-10-22T09:00:00"),
    ], now);
    expect(groups.map((group) => [group.label, group.items.map((entry) => entry.id)])).toEqual([
      ["今天", ["today-late", "today-early"]], ["昨天", ["yesterday"]], ["这 7 天", ["week"]], ["更早", ["old", "broken"]],
    ]);
  });

  it("没有资料就没有分组", () => {
    expect(groupArtifacts([], now)).toEqual([]);
  });

  it("行尾时间：今天写时分，今年写月日，往年带年份", () => {
    expect(artifactTimeLabel("2026-10-28T09:05:00", now)).toBe("09:05");
    expect(artifactTimeLabel("2026-03-02T09:05:00", now)).toBe("3月2日");
    expect(artifactTimeLabel("2025-12-31T09:05:00", now)).toBe("2025/12/31");
    expect(artifactTimeLabel("", now)).toBe("");
  });
});
