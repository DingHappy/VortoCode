import { describe, expect, it } from "vitest";
import { searchSettings, settingsPage } from "./settingsNavigation";

describe("settings navigation", () => {
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
});
