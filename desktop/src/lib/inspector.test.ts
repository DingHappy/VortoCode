import { describe, expect, it } from "vitest";

import { autoFocusDecision } from "./inspector";

describe("autoFocusDecision", () => {
  it("面板没开：后台动静可以把它打开", () => {
    expect(autoFocusDecision({ open: false, current: "inbox", target: "diff" })).toBe("open");
  });

  it("正在看别的标签：只标提示点，不抢走视线（真机 2026-09-17）", () => {
    expect(autoFocusDecision({ open: true, current: "files", target: "diff" })).toBe("mark");
    expect(autoFocusDecision({ open: true, current: "files", target: "inbox" })).toBe("mark");
  });

  it("正好就在目标标签上：什么都不用做", () => {
    expect(autoFocusDecision({ open: true, current: "diff", target: "diff" })).toBe("ignore");
  });
});
