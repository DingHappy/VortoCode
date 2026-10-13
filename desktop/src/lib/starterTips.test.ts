import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { STORAGE_KEYS } from "./storage";
import {
  STARTER_TIPS_GRADUATE_AFTER,
  dismissStarterTips,
  recordAssistantSend,
  shouldShowStarterTips,
  starterTips,
} from "./starterTips";

function memoryStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => { map.set(key, String(value)); },
    removeItem: (key: string) => { map.delete(key); },
    clear: () => map.clear(),
  };
}

beforeEach(() => { vi.stubGlobal("localStorage", memoryStorage()); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("starterTips", () => {
  it("本机通用会话不列定时汇报、派活——那里没有调度也没有项目", () => {
    const ids = starterTips({ resident: false, projects: ["VortoCode"] }).map((tip) => tip.id);
    expect(ids).not.toContain("brief");
    expect(ids).not.toContain("dispatch");
    expect(ids).toContain("remember");
  });

  it("常驻电脑上：派活示例用真实登记的项目名；没有项目就不列派活", () => {
    const withProject = starterTips({ resident: true, projects: ["", "VortoCode"] });
    expect(withProject.find((tip) => tip.id === "dispatch")?.prompt).toContain("VortoCode 项目");
    expect(withProject[0].id).toBe("brief");
    expect(starterTips({ resident: true, projects: [] }).map((tip) => tip.id)).not.toContain("dispatch");
  });

  it("每条示例都有能直接发的文字", () => {
    for (const tip of [...starterTips({ resident: true, projects: ["P"] }), ...starterTips({ resident: false, projects: [] })]) {
      expect(tip.label.trim()).not.toBe("");
      expect(tip.prompt.trim().length).toBeGreaterThan(8);
    }
  });
});

describe("显示与收起", () => {
  it("新用户显示；发够几条后收起", () => {
    expect(shouldShowStarterTips()).toBe(true);
    for (let i = 0; i < STARTER_TIPS_GRADUATE_AFTER - 1; i += 1) recordAssistantSend();
    expect(shouldShowStarterTips()).toBe(true);
    recordAssistantSend();
    expect(shouldShowStarterTips()).toBe(false);
  });

  it("点了不再显示就一直不显示", () => {
    dismissStarterTips();
    expect(shouldShowStarterTips()).toBe(false);
    expect(JSON.parse(localStorage.getItem(STORAGE_KEYS.starterTips) ?? "{}").dismissed).toBe(true);
  });

  it("存储坏了或读不了：照常显示，不抛错", () => {
    localStorage.setItem(STORAGE_KEYS.starterTips, "{not json");
    expect(shouldShowStarterTips()).toBe(true);
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } });
    expect(shouldShowStarterTips()).toBe(true);
    expect(() => { recordAssistantSend(); dismissStarterTips(); }).not.toThrow();
  });
});
