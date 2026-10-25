import { describe, expect, it, vi } from "vitest";
import { createResidentDesktopStore, desktopConnectionLabel, type ResidentDesktopStatus } from "./residentDesktop";

const status = (overrides: Partial<ResidentDesktopStatus> = {}): ResidentDesktopStatus => ({ target: "ssh:vm", sshConnected: true,
  viewerUrl: null, computer: { ready: true, mode: "human", viewers: 0, passwordStore: "unlocked", observerReady: true, observers: 0 },
  checkedAt: 100, error: null, ...overrides });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }

describe("native desktop connection state", () => {
  it("discards late reads and deduplicates actions across both entry points", async () => {
    const read = deferred<ResidentDesktopStatus>(), open = deferred<ResidentDesktopStatus>();
    const call = vi.fn().mockResolvedValue(status({ target: "ssh:new" }))
      .mockImplementationOnce(() => read.promise).mockImplementationOnce(() => open.promise);
    const store = createResidentDesktopStore(call);
    const pending = store.refresh();
    const opening = store.act("open_resident_desktop");
    await store.act("close_resident_desktop");
    read.resolve(status({ target: "ssh:old" })); await pending;
    expect(store.getSnapshot().busy).toBe(true);
    expect(store.getSnapshot().status).toBeNull();
    open.resolve(status({ target: "ssh:new" })); await opening;
    expect(store.getSnapshot().status?.target).toBe("ssh:new");
    expect(call.mock.calls.map(args => args[0])).toEqual(["get_resident_desktop", "open_resident_desktop"]);
  });
  it("reconciles a window-close notification received while open is completing", async () => {
    const open = deferred<ResidentDesktopStatus>();
    const call = vi.fn().mockImplementationOnce(() => open.promise)
      .mockResolvedValue(status({ sshConnected: false }));
    const store = createResidentDesktopStore(call);
    const opening = store.act("open_resident_desktop");
    await store.refresh(); await store.refresh(); // coalesce both busy events
    expect(call).toHaveBeenCalledTimes(1);
    open.resolve(status({ viewerUrl: "http://127.0.0.1:1234/" })); await opening;
    await vi.waitFor(() => expect(store.getSnapshot().status?.sshConnected).toBe(false));
    expect(call.mock.calls.map(args => args[0])).toEqual(["open_resident_desktop", "get_resident_desktop"]);
  });
  it("a passive read never blocks disconnect or resurrects its old success", async () => {
    const poll = deferred<ResidentDesktopStatus>();
    const call = vi.fn().mockResolvedValueOnce(status()).mockImplementationOnce(() => poll.promise)
      .mockResolvedValueOnce(status({ sshConnected: false }));
    const store = createResidentDesktopStore(call); await store.refresh();
    const reading = store.poll();
    await store.act("close_resident_desktop");
    poll.resolve(status()); await reading;
    expect(store.getSnapshot().status?.sshConnected).toBe(false);
    expect(call.mock.calls.map(args => args[0])).toEqual(["get_resident_desktop", "refresh_resident_desktop", "close_resident_desktop"]);
  });
  it("invalidates readiness on read failure and clears the read error on recovery", async () => {
    const call = vi.fn().mockResolvedValueOnce(status()).mockRejectedValueOnce("IPC 已断线").mockResolvedValueOnce(status());
    const store = createResidentDesktopStore(call); await store.refresh();
    expect(await store.poll()).toBe(false);
    expect(desktopConnectionLabel(store.getSnapshot().status, 200)).toBe("连接待检查");
    expect(store.getSnapshot().note).toBe("IPC 已断线");
    await store.poll(); expect(store.getSnapshot().note).toBe("");
    expect(desktopConnectionLabel(store.getSnapshot().status, 200)).toBe("桌面就绪");
  });
  it("a failed open refreshes authoritative metadata and never keeps an old success label", async () => {
    const call = vi.fn().mockResolvedValueOnce(status()).mockRejectedValueOnce("SSH 超时")
      .mockResolvedValueOnce(status({ error: "SSH 超时" }));
    const store = createResidentDesktopStore(call);
    await store.refresh(); await store.act("open_resident_desktop");
    expect(store.getSnapshot().note).toBe("SSH 超时");
    expect(desktopConnectionLabel(store.getSnapshot().status, 200)).toBe("连接待检查");
    expect(store.getSnapshot().busy).toBe(false);
  });
  it("requires both a live owned transport and recent successful metadata", () => {
    expect(desktopConnectionLabel(status(), 200)).toBe("桌面就绪");
    expect(desktopConnectionLabel(status(), 60_100)).toBe("连接状态待刷新");
    expect(desktopConnectionLabel(status(), 50)).toBe("连接状态待刷新");
    expect(desktopConnectionLabel(status({ sshConnected: false }), 200)).toBe("未连接");
    expect(desktopConnectionLabel(status({ computer: { ...status().computer!, observerReady: false } }), 200)).toBe("画面入口尚未就绪");
  });
});
