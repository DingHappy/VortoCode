import { describe, expect, it } from "vitest";

import { addContextItem, contextItemLabel, MAX_CONTEXT_ITEMS, selectSourceLine, visibleWorkspaceFiles } from "./sourceContext";

describe("source context references", () => {
  it("labels a line range and a whole file", () => {
    expect(contextItemLabel({ path: "src/a.ts", startLine: 3, endLine: 9 })).toBe("src/a.ts:L3–L9");
    expect(contextItemLabel({ path: "src/a.ts" })).toBe("src/a.ts");
  });

  it("a whole file replaces its ranges; a range replaces only the whole-file reference", () => {
    const ranges = [{ path: "a.ts", startLine: 1, endLine: 2 }, { path: "a.ts", startLine: 5, endLine: 6 }, { path: "b.ts" }];
    expect(addContextItem(ranges, { path: "a.ts" }).items).toEqual([{ path: "b.ts" }, { path: "a.ts" }]);
    const whole = [{ path: "a.ts" }, { path: "b.ts" }];
    expect(addContextItem(whole, { path: "a.ts", startLine: 1, endLine: 2 }).items)
      .toEqual([{ path: "b.ts" }, { path: "a.ts", startLine: 1, endLine: 2 }]);
  });

  it("adding the same reference twice keeps the list as it was", () => {
    const items = [{ path: "a.ts", startLine: 1, endLine: 2 }];
    const result = addContextItem(items, { path: "a.ts", startLine: 1, endLine: 2 });
    expect(result.items).toBe(items);
    expect(result.full).toBe(false);
  });

  it("stops at the per-turn limit without changing the list", () => {
    const items = Array.from({ length: MAX_CONTEXT_ITEMS }, (_, index) => ({ path: `f${index}.ts` }));
    const result = addContextItem(items, { path: "extra.ts" });
    expect(result).toEqual({ items, full: true });
    // 顶掉同文件引用后没超限，照常加入
    expect(addContextItem(items, { path: "f0.ts", startLine: 1, endLine: 1 }).full).toBe(false);
  });
});

describe("source line selection", () => {
  it("clicking the only selected line clears it; clicking another line starts over", () => {
    const one = selectSourceLine(null, 10, false);
    expect(one).toEqual({ anchor: 10, start: 10, end: 10 });
    expect(selectSourceLine(one, 10, false)).toBeNull();
    expect(selectSourceLine(one, 12, false)).toEqual({ anchor: 12, start: 12, end: 12 });
  });

  it("extending keeps the anchor and caps the span", () => {
    const anchor = { anchor: 100, start: 100, end: 100 };
    expect(selectSourceLine(anchor, 90, true)).toEqual({ anchor: 100, start: 90, end: 100 });
    expect(selectSourceLine(anchor, 2000, true)).toEqual({ anchor: 100, start: 100, end: 599 });
    expect(selectSourceLine(null, 7, true)).toEqual({ anchor: 7, start: 7, end: 7 });
  });
});

it("hides VortoCode's own directory and filters case-insensitively", () => {
  const files = [".vortocode", ".vortocode/memory/repo.md", "src/App.tsx", "README.md"];
  expect(visibleWorkspaceFiles(files, "")).toEqual(["src/App.tsx", "README.md"]);
  expect(visibleWorkspaceFiles(files, "  app ")).toEqual(["src/App.tsx"]);
  expect(visibleWorkspaceFiles(files, "repo")).toEqual([]);
});
