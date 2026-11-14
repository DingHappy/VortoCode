import { expect, it } from "vitest";
import { matchesCollectionQuery } from "./collectionQuery";
import { filterArtifacts } from "./libraryGroups";
import { filterRoutineItems } from "./routineList";
import { routineFixture } from "../../qa/routine-fixture";
import type { ArtifactMeta } from "../types";

it("matches all normalized search terms across loaded metadata, ignoring case and full-width Latin", () => {
  expect(matchesCollectionQuery("  ＡＬＰＨＡ   摘要 ", ["Alpha 周报摘要"])).toBe(true);
  expect(matchesCollectionQuery("Alpha missing", ["Alpha 周报摘要"])).toBe(false);
  expect(matchesCollectionQuery(" \n ", [])).toBe(true);
});

it("combines library title search and kind, preserving order and metadata", () => {
  const items: ArtifactMeta[] = [
    { id: "a", title: "Alpha 摘要", kind: "html", version: 1, bytes: 1, created_at: "", updated_at: "", url: "https://example.com/private-name" },
    { id: "b", title: "Alpha 文档", kind: "markdown", version: 2, bytes: 2, created_at: "", updated_at: "", url: "" },
  ];
  const before = structuredClone(items);
  expect(filterArtifacts(items, "alpha", "markdown").map(item => item.id)).toEqual(["b"]);
  expect(filterArtifacts(items, "", "all").map(item => item.id)).toEqual(["a", "b"]);
  expect(filterArtifacts(items, "private-name", "all")).toEqual([]);
  expect(items).toEqual(before);
});

it("filters routines by name, registered member and state without changing enablement or errors", () => {
  const first = routineFixture().items[0];
  const items = [first, { ...first, id: "paused", name: "每周回顾", enabled: false, state: { ...first.state, error: "需主人核对" } },
    { ...first, id: "deleted", name: "旧回顾", retired: true }];
  const before = structuredClone(items);
  expect(filterRoutineItems(items, "回顾", "all").map(item => item.id)).toEqual([first.id, "paused"]);
  expect(filterRoutineItems(items, first.member_name, "paused").map(item => item.id)).toEqual(["paused"]);
  expect(filterRoutineItems(items, "", "enabled").map(item => item.id)).toEqual([first.id]);
  expect(filterRoutineItems(items, "旧回顾", "all")).toEqual([]);
  expect(items).toEqual(before);
});
