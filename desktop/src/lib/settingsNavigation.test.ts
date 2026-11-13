import { describe, expect, it } from "vitest";
import { searchSettings, settingsPage, SETTINGS_ITEMS, SETTINGS_OWNER_LABELS } from "./settingsNavigation";

describe("settings navigation", () => {
  it("declares an owner for every searchable setting and keeps local and runtime writes distinct", () => {
    for (const item of SETTINGS_ITEMS) expect(SETTINGS_OWNER_LABELS[item.owner]).toBeTruthy();
    expect(SETTINGS_ITEMS.find(item => item.id === "services")?.owner).toBe("desktop");
    expect(SETTINGS_ITEMS.find(item => item.id === "staff")?.owner).toBe("space");
    expect(SETTINGS_ITEMS.find(item => item.id === "trust")?.owner).toBe("workspace");
    expect(searchSettings("这台 Mac 模型").map(item => item.id)).toContain("services");
  });
  it("keeps the previous abilities entry pointing at workspace permissions", () => {
    expect(settingsPage("abilities")).toBe("permissions");
    expect(settingsPage("computer")).toBe("computer");
  });
  it("separates remote service, screen connection and desktop action permissions", () => {
    expect(searchSettings("远程").map(item => [item.id, item.page])).toContainEqual(["resident", "computer"]);
    expect(searchSettings("远程").map(item => [item.id, item.page])).toContainEqual(["desktop", "computer"]);
    expect(searchSettings("接管").map(item => item.id)).toEqual(["desktop"]);
    expect(searchSettings("桌面 权限").map(item => item.id)).toEqual(["control"]);
  });
  it("finds a setting item rather than only its category", () => {
    expect(searchSettings(" API KEY ").map(item => item.id)).toEqual(["services"]);
    expect(searchSettings("codex").map(item => item.id)).toEqual(["roles", "codex"]);
    expect(searchSettings("  ")).toEqual([]);
    expect(searchSettings("does-not-exist")).toEqual([]);
  });
  it("finds local resident setup separately from remote connections", () => {
    expect(searchSettings("本机 常驻").map(item => item.id)).toEqual(["local-resident"]);
    expect(searchSettings("登录 后台").map(item => item.id)).toEqual(["local-resident"]);
    expect(searchSettings("远程").map(item => item.id)).toContain("resident");
  });
});
