import { describe, expect, it } from "vitest";

import { localDay, previousDay, scheduleLabel, whenLabel } from "./time";

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

describe("whenLabel / scheduleLabel", () => {
  const now = new Date(2026, 9, 27, 21, 0);
  it("近三天写今天/昨天/明天，其余写月日，跨年带年份", () => {
    expect(whenLabel("2026-10-27T20:25:00", now)).toBe("今天 20:25");
    expect(whenLabel("2026-10-26T08:05:00", now)).toBe("昨天 08:05");
    expect(whenLabel("2026-10-28T09:00:00", now)).toBe("明天 09:00");
    expect(whenLabel("2026-10-20T09:00:00", now)).toBe("10月20日 09:00");
    expect(whenLabel("2025-12-31T23:59:00", now)).toBe("2025年12月31日 23:59");
  });
  it("带时区的时间按来源时区读，不换算成本机时区", () => {
    const shanghaiEvening = new Date(Date.UTC(2026, 9, 27, 13, 0)); // 上海 10-27 21:00
    expect(whenLabel("2026-10-27T20:25:00+08:00", shanghaiEvening)).toBe("今天 20:25");
    expect(whenLabel("2026-10-28T08:00:00+0800", shanghaiEvening)).toBe("明天 08:00");
    expect(whenLabel("2026-10-27T12:25:00Z", shanghaiEvening)).toBe("今天 12:25");
  });
  it("解析不了的原样返回", () => {
    expect(whenLabel("未知", now)).toBe("未知");
    expect(whenLabel("2026-10-27 20:25", now)).toBe("2026-10-27 20:25");
  });
  it("周期统一成中文读法", () => {
    expect(scheduleLabel("每 1 分钟", now)).toBe("每分钟");
    expect(scheduleLabel("每 30m", now)).toBe("每 30 分钟");
    expect(scheduleLabel("every 1h", now)).toBe("每小时");
    expect(scheduleLabel("every 2h", now)).toBe("每 2 小时");
    expect(scheduleLabel("每天 09:00", now)).toBe("每天 09:00");
    expect(scheduleLabel("一次 · 2026-10-28T09:00:00", now)).toBe("一次 · 明天 09:00");
    expect(scheduleLabel("0 9 * * 1-5", now)).toBe("0 9 * * 1-5");
  });
});
