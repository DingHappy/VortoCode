import { describe, expect, it } from "vitest";

import type { TrustStatus } from "../types";
import { trustCard } from "./trustCard";

const status = (over: Partial<TrustStatus> = {}): TrustStatus => ({
  level: "ask",
  effective: "ask",
  ceiling: "full",
  levels: ["ask", "reads", "full"],
  capability_profile: "local",
  workspace_scope: "project",
  ...over,
});

describe("授权档位卡", () => {
  it("拿不到档位就整张卡不显示", () => {
    // 回归：General 会话下 /api/trust 返回 409，前端 catch 成 null。此前卡片照常渲染，
    // tooltip 里的 `TRUST_LABELS[trust!.ceiling]` 在运行时读 null.ceiling → 打开设置白屏。
    // 而 General 正是启动后的默认状态。
    expect(trustCard(null)).toBeNull();
    expect(trustCard(undefined)).toBeNull();
  });

  it("后端给了不认识的档位名也不渲染，而不是崩", () => {
    expect(trustCard(status({ ceiling: "root" as never }))).toBeNull();
    expect(trustCard(status({ effective: "" as never }))).toBeNull();
  });

  it("正常情况下三档都在，当前档标记为选中", () => {
    const card = trustCard(status({ level: "reads", effective: "reads" }));
    expect(card?.options.map((option) => option.level)).toEqual(["ask", "reads", "full"]);
    expect(card?.options.find((option) => option.active)?.level).toBe("reads");
    expect(card?.title).toBe("只读免确认");
  });

  it("超过上限的档位禁用，并说清为什么", () => {
    const card = trustCard(status({ ceiling: "reads", capability_profile: "external" }));
    const full = card?.options.find((option) => option.level === "full");
    expect(full?.blocked).toBe(true);
    expect(full?.blockedReason).toContain("external");
    expect(full?.blockedReason).toContain("只读免确认");
    expect(card?.options.find((option) => option.level === "ask")?.blocked).toBe(false);
  });

  it("选的档位被上限夹住时，说明按哪一档执行", () => {
    const card = trustCard(status({ level: "full", effective: "reads", ceiling: "reads" }));
    expect(card?.note).toContain("已选「完全信任」");
    expect(card?.note).toContain("按后者执行");
  });

  it("没被夹住时讲的是污点回合那条铁律", () => {
    expect(trustCard(status())?.note).toContain("防提示注入");
  });

  it("后端只给两档就只画两档", () => {
    const card = trustCard(status({ levels: ["ask", "reads"], ceiling: "reads" }));
    expect(card?.options.map((option) => option.level)).toEqual(["ask", "reads"]);
  });
});
