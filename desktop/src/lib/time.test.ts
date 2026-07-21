import { describe, expect, it } from "vitest";

import { localDay, previousDay } from "./time";

describe("previousDay", () => {
  it("普通日期回退一天", () => {
    expect(previousDay("2026-07-21")).toBe("2026-07-20");
  });

  it("跨月/闰年/跨年边界", () => {
    expect(previousDay("2026-03-01")).toBe("2026-02-28");
    expect(previousDay("2024-03-01")).toBe("2024-02-29");
    expect(previousDay("2026-01-01")).toBe("2025-12-31");
  });
});

describe("localDay", () => {
  it("输出本地时区的 YYYY-MM-DD", () => {
    expect(localDay()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // previousDay(localDay()) 必须仍是合法日期串（两函数以同一格式互通）
    expect(previousDay(localDay())).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
