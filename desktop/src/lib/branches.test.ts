import { describe, expect, it } from "vitest";

import { isProtectedBranch } from "./branches";

describe("受保护分支判定", () => {
  it("认得出常见的默认分支，且不区分大小写", () => {
    expect(isProtectedBranch("main")).toBe(true);
    expect(isProtectedBranch("Main")).toBe(true);
    expect(isProtectedBranch(" master ")).toBe(true);
    expect(isProtectedBranch("develop")).toBe(true);
  });

  it("功能分支不算", () => {
    expect(isProtectedBranch("vorto/fix-x")).toBe(false);
    expect(isProtectedBranch("maintenance")).toBe(false);   // 不是 main 的前缀匹配
    expect(isProtectedBranch("")).toBe(false);
    expect(isProtectedBranch(undefined)).toBe(false);
  });
});
