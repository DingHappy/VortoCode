import { describe, expect, it } from "vitest";

import { autoFocusDecision, closeWorkbenchTab, openWorkbenchTab } from "./inspector";

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

describe("workbench tabs", () => {
  it("打开：新工具追加到末尾，已打开的原位不动，任务中心页面不进标签栏", () => {
    expect(openWorkbenchTab([], "info")).toEqual(["info"]);
    expect(openWorkbenchTab(["info", "diff"], "files")).toEqual(["info", "diff", "files"]);
    expect(openWorkbenchTab(["info", "diff"], "info")).toEqual(["info", "diff"]);
    expect(openWorkbenchTab(["info"], "inbox")).toEqual(["info"]);
  });

  it("关掉当前标签：先看右边，没有右边看左边，全关完回到工具列表", () => {
    expect(closeWorkbenchTab(["info", "diff", "files"], "diff", "diff")).toEqual({ tabs: ["info", "files"], next: "files" });
    expect(closeWorkbenchTab(["info", "diff", "files"], "files", "files")).toEqual({ tabs: ["info", "diff"], next: "diff" });
    expect(closeWorkbenchTab(["info"], "info", "info")).toEqual({ tabs: [], next: null });
  });

  it("关掉别的标签不打断正在看的；关一个不存在的标签什么都不变", () => {
    expect(closeWorkbenchTab(["info", "diff"], "info", "diff")).toEqual({ tabs: ["diff"], next: "diff" });
    expect(closeWorkbenchTab(["info"], "runs", "info")).toEqual({ tabs: ["info"], next: "info" });
  });
});
