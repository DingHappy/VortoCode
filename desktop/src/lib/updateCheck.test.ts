import { describe, expect, it, vi } from "vitest";

import { AUTO_CHECK_INTERVAL_MS, autoCheckDue } from "./updateCheck";

describe("autoCheckDue", () => {
  const now = 1_800_000_000_000;
  it("checks at most once a day", () => {
    expect(autoCheckDue(null, now)).toBe(true);
    expect(autoCheckDue(now - 1000, now)).toBe(false);
    expect(autoCheckDue(now - AUTO_CHECK_INTERVAL_MS, now)).toBe(true);
  });
  it("recovers from bad records and clocks moved backwards", () => {
    expect(autoCheckDue(Number.NaN, now)).toBe(true);
    expect(autoCheckDue(now + 60_000, now)).toBe(true);
  });
});

describe("installUpdate", () => {
  it("restarts only after the update installed", async () => {
    const calls: string[] = [];
    vi.resetModules();
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: async (command: string) => {
        calls.push(command);
        if (command === "install_update") return "0.2.14";
        return undefined;
      },
    }));
    const { installUpdate } = await import("./updateCheck");
    await expect(installUpdate()).resolves.toBe("0.2.14");
    expect(calls).toEqual(["install_update", "restart_after_update"]);
  });

  it("never restarts when the owner declines or the signature check fails", async () => {
    const calls: string[] = [];
    vi.resetModules();
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: async (command: string) => {
        calls.push(command);
        throw new Error("已取消，稍后可在设置里更新");
      },
    }));
    const { installUpdate } = await import("./updateCheck");
    await expect(installUpdate()).rejects.toThrow("已取消");
    expect(calls).toEqual(["install_update"]);
  });
});
