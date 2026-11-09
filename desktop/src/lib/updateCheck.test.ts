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
  it("leaves the restart to the native side once the update installed", async () => {
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
    // 网页进程可能在替换 App 时已经退出，重启不能靠网页再发一次请求
    expect(calls).toEqual(["install_update"]);
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

describe("progressText", () => {
  it("shows percent, megabytes, retries and the install phase", async () => {
    const { progressText } = await import("./updateCheck");
    expect(progressText(null)).toBe("正在更新…");
    expect(progressText({ downloaded: 19_000_000, total: 38_000_000, attempt: 1 })).toBe("正在下载 50%");
    expect(progressText({ downloaded: 0, total: 38_000_000, attempt: 2 })).toBe("正在下载 0%（第 2 次）");
    expect(progressText({ downloaded: 3 * 1048576, total: null, attempt: 1 })).toBe("正在下载 3.0 MB");
    expect(progressText({ downloaded: 38_000_000, total: 38_000_000, attempt: 3 })).toBe("正在安装…");
  });
});

describe("installUpdate progress", () => {
  it("forwards progress events while installing and stops listening afterwards", async () => {
    let handler: ((event: { payload: unknown }) => void) | undefined;
    const unlisten = vi.fn();
    vi.resetModules();
    vi.doMock("@tauri-apps/api/event", () => ({
      listen: async (name: string, callback: (event: { payload: unknown }) => void) => {
        expect(name).toBe("update-progress");
        handler = callback;
        return unlisten;
      },
    }));
    vi.doMock("@tauri-apps/api/core", () => ({
      invoke: async (command: string) => {
        if (command === "install_update") {
          handler?.({ payload: { downloaded: 5, total: 10, attempt: 1 } });
          throw new Error("下载更新包失败（试了 3 次）");
        }
        return undefined;
      },
    }));
    const { installUpdate } = await import("./updateCheck");
    const seen: unknown[] = [];
    await expect(installUpdate((progress) => seen.push(progress))).rejects.toThrow("试了 3 次");
    expect(seen).toEqual([{ downloaded: 5, total: 10, attempt: 1 }]);
    expect(unlisten).toHaveBeenCalledOnce();
    vi.doUnmock("@tauri-apps/api/event");
  });
});
