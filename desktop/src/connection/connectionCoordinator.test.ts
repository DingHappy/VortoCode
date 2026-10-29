import { describe, expect, it, vi } from "vitest";
import { residentRetryReason, residentRetrySettled, ConnectionCoordinator, localConnectUrl, placeAssistant, residentRetryDelay, retryResident } from "./connectionCoordinator";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const tick = (ms = 1) => new Promise<void>((resolve) => setTimeout(resolve, ms));

describe("ConnectionCoordinator", () => {
  it("只允许一个切换：并发的第二次不执行，结束后通知界面", async () => {
    const changes: boolean[] = [];
    const coordinator = new ConnectionCoordinator({ switching: (active) => changes.push(active) });
    const gate = deferred<boolean>();
    const second = vi.fn(async () => true);
    const first = coordinator.exclusiveSwitch(() => gate.promise);
    expect(coordinator.switching).toBe(true);
    expect(await coordinator.exclusiveSwitch(second)).toBe(false);
    expect(second).not.toHaveBeenCalled();
    gate.resolve(true);
    expect(await first).toBe(true);
    expect(coordinator.switching).toBe(false);
    expect(changes).toEqual([true, false]);
  });

  it("切换抛错也会放开门", async () => {
    const coordinator = new ConnectionCoordinator();
    await expect(coordinator.exclusiveSwitch(async () => { throw new Error("boom"); })).rejects.toThrow("boom");
    expect(coordinator.switching).toBe(false);
    expect(await coordinator.exclusiveSwitch(async () => true)).toBe(true);
  });

  it("本机引擎只启动一个", async () => {
    const changes: boolean[] = [];
    const coordinator = new ConnectionCoordinator({ starting: (active) => changes.push(active) });
    const gate = deferred<boolean>();
    const first = coordinator.exclusiveStart(() => gate.promise);
    expect(coordinator.busy).toBe(true);
    expect(await coordinator.exclusiveStart(async () => true)).toBe(false);
    gate.resolve(true);
    expect(await first).toBe(true);
    expect(changes).toEqual([true, false]);
  });

  it("等空闲：忙完返回 true，超时仍忙返回 false", async () => {
    const coordinator = new ConnectionCoordinator();
    const gate = deferred<boolean>();
    const start = coordinator.exclusiveStart(() => gate.promise);
    const waits: number[] = [];
    expect(await coordinator.waitUntilIdle(3, 1, async (ms) => { waits.push(ms); })).toBe(false);
    expect(waits).toEqual([1, 1, 1]);
    const idle = coordinator.waitUntilIdle(1_000, 1);
    gate.resolve(true);
    await start;
    expect(await idle).toBe(true);
  });

  it("只有真失败才记下（连同原因）；新一轮连接清掉旧记录", () => {
    const coordinator = new ConnectionCoordinator();
    coordinator.connectBegan();
    expect(coordinator.lastConnectFailed).toBe(false);
    coordinator.connectFailed("远程 WebSocket 连接失败：本地网络");
    expect(coordinator.lastConnectFailed).toBe(true);
    expect(coordinator.lastConnectError).toContain("本地网络");
    coordinator.connectBegan();
    expect(coordinator.lastConnectFailed).toBe(false);
    expect(coordinator.lastConnectError).toBeNull();
  });

  it("连着的远端就是常驻助理时才算在常驻电脑上", () => {
    const coordinator = new ConnectionCoordinator();
    expect(coordinator.onAssistantHost).toBe(false);
    coordinator.remote.current = "host";
    expect(coordinator.onAssistantHost).toBe(false);
    coordinator.assistantHost.current = "host";
    expect(coordinator.onAssistantHost).toBe(true);
    coordinator.remote.current = "project";
    expect(coordinator.onAssistantHost).toBe(false);
  });
});

describe("placeAssistant", () => {
  const host = { id: "resident", name: "家里服务器" };
  const base = () => ({
    connectHost: vi.fn(async (): Promise<true | string> => true), awayFromLocalGeneral: vi.fn(() => true),
    switchToLocalGeneral: vi.fn(async () => true), onHostFailure: vi.fn(),
  });

  it("常驻电脑连上就住在那里，不碰本机", async () => {
    const deps = base();
    expect(await placeAssistant({ ...deps, host })).toBe("resident");
    expect(deps.switchToLocalGeneral).not.toHaveBeenCalled();
    expect(deps.onHostFailure).not.toHaveBeenCalled();
  });

  it("常驻电脑连不上：把真实原因交出去，并退回本机通用工作区", async () => {
    const deps = { ...base(), connectHost: vi.fn(async (): Promise<true | string> => "macOS「本地网络」权限没开") };
    expect(await placeAssistant({ ...deps, host })).toBeNull();
    expect(deps.onHostFailure).toHaveBeenCalledWith(host, "macOS「本地网络」权限没开");
    expect(deps.switchToLocalGeneral).toHaveBeenCalledOnce();
  });

  it("没登记常驻电脑、本来就在本机通用工作区：原地不动", async () => {
    const deps = { ...base(), awayFromLocalGeneral: vi.fn(() => false) };
    expect(await placeAssistant(deps)).toBeNull();
    expect(deps.connectHost).not.toHaveBeenCalled();
    expect(deps.switchToLocalGeneral).not.toHaveBeenCalled();
  });

  it("两边都没落下返回 undefined", async () => {
    const deps = { ...base(), connectHost: vi.fn(async (): Promise<true | string> => "离线"), switchToLocalGeneral: vi.fn(async () => false) };
    expect(await placeAssistant({ ...deps, host })).toBeUndefined();
  });
});

// 下面两个场景按 App.tsx 的调用方式组装：连常驻电脑先等本机空闲，切本机范围在引擎启动中先拒绝。
describe("切换场景", () => {
  function app(coordinator: ConnectionCoordinator, residentOnline: boolean) {
    const log: string[] = [];
    const connectHost = async (): Promise<true | string> => {
      if (!await coordinator.waitUntilIdle(1_000, 1)) return "本机忙";
      return await coordinator.exclusiveSwitch(async () => { log.push("resident"); return residentOnline; }) || "离线";
    };
    const switchToLocalGeneral = async () => {
      if (coordinator.starting) { log.push("refused: starting"); return false; }
      return coordinator.exclusiveSwitch(async () => { log.push("local"); return true; });
    };
    return { log, connectHost, switchToLocalGeneral };
  }

  it("P1 场景 A：冷启动时常驻电脑离线，等本机启动完再连，连不上就退回本机", async () => {
    const coordinator = new ConnectionCoordinator();
    const launch = deferred<boolean>();
    const startup = coordinator.exclusiveStart(() => launch.promise);
    const { log, connectHost, switchToLocalGeneral } = app(coordinator, false);
    const placed = placeAssistant({ host: { id: "resident", name: "家里服务器" }, connectHost, switchToLocalGeneral,
      awayFromLocalGeneral: () => true, onHostFailure: () => {} });
    await tick(5);
    expect(log).toEqual([]);   // 本机还在启动：不去抢
    launch.resolve(true);
    await startup;
    expect(await placed).toBeNull();
    expect(log).toEqual(["resident", "local"]);
  });

  it("确认框期间另一次切换抢先进来：后到的不会叠上", async () => {
    const coordinator = new ConnectionCoordinator();
    const confirm = deferred<void>();
    const bodies: string[] = [];
    const switchWithConfirm = async (name: string) => {
      if (coordinator.switching) return false;     // 调用方的先行检查
      await confirm.promise;                       // 「有未保存修改，确定继续吗？」
      return coordinator.exclusiveSwitch(async () => { bodies.push(name); await tick(); return true; });
    };
    const a = switchWithConfirm("a");
    const b = switchWithConfirm("b");
    confirm.resolve();
    expect((await Promise.all([a, b])).sort()).toEqual([false, true]);
    expect(bodies).toHaveLength(1);
  });
});

describe("常驻电脑后台重连", () => {
  it("退避 10 秒、30 秒、1 分钟，之后每 2 分钟一直试", () => {
    expect([0, 1, 2, 3, 4, 50].map(residentRetryDelay)).toEqual([10_000, 30_000, 60_000, 120_000, 120_000, 120_000]);
  });

  it("测不通就只记原因，不断开本机、不切换", async () => {
    const connect = vi.fn(async () => true);
    const onUnreachable = vi.fn();
    expect(await retryResident({ reachable: async () => "这台 Mac 拒绝直连过去", connect, onUnreachable })).toBe(false);
    expect(connect).not.toHaveBeenCalled();
    expect(onUnreachable).toHaveBeenCalledWith("这台 Mac 拒绝直连过去");
  });

  it("测通了才走完整切换", async () => {
    const connect = vi.fn(async () => true);
    expect(await retryResident({ reachable: async () => null, connect, onUnreachable: vi.fn() })).toBe(true);
    expect(connect).toHaveBeenCalledOnce();
  });
});

describe("residentRetryReason", () => {
  const base = { assistantMode: true, hasHost: true, onHost: true, connection: "connected" as const, switching: false };
  it("retries when the assistant fell back to this Mac, and when a resident connection dropped", () => {
    expect(residentRetryReason({ ...base, onHost: false })).toBe("missed");
    expect(residentRetryReason({ ...base, connection: "error" })).toBe("dropped");
  });
  it("leaves healthy, intentionally left, switching, workbench and unregistered states alone", () => {
    expect(residentRetryReason(base)).toBeNull();
    expect(residentRetryReason({ ...base, connection: "disconnected" })).toBeNull();
    expect(residentRetryReason({ ...base, connection: "connecting" })).toBeNull();
    expect(residentRetryReason({ ...base, connection: "error", switching: true })).toBeNull();
    expect(residentRetryReason({ ...base, connection: "error", assistantMode: false })).toBeNull();
    expect(residentRetryReason({ ...base, onHost: false, hasHost: false })).toBeNull();
    expect(residentRetryReason({ ...base, onHost: false, connection: "disconnected" })).toBeNull();
  });
  it("keeps retrying the resident computer when the local fallback is broken too", () => {
    // 真机 2026-10-28：断线后退回本机又连坏了，两头都没连上，网络恢复后也不再试
    expect(residentRetryReason({ ...base, onHost: false, connection: "error" })).toBe("missed");
  });
});

describe("localConnectUrl", () => {
  it("replaces a stale resident address with the last local one", () => {
    expect(localConnectUrl("http://192.168.1.2:8081", ["http://192.168.1.2:8081"], "http://127.0.0.1:8090")).toBe("http://127.0.0.1:8090");
    expect(localConnectUrl("http://192.168.1.2:8081", ["http://192.168.1.2:8081"], null)).toBe("http://127.0.0.1:8080");
  });
  it("leaves any other address for the connection to validate", () => {
    expect(localConnectUrl("http://127.0.0.1:8091", ["http://192.168.1.2:8081"], "http://127.0.0.1:8090")).toBe("http://127.0.0.1:8091");
    expect(localConnectUrl("http://10.0.0.9:8080", ["http://192.168.1.2:8081"], "http://127.0.0.1:8090")).toBe("http://10.0.0.9:8080");
  });
});

describe("residentRetrySettled", () => {
  it("重连途中经过「连接中」不算连上，次数不清零，退避才会变长", () => {
    expect(residentRetrySettled({ onHost: true, connection: "connecting" })).toBe(false);
    expect(residentRetrySettled({ onHost: true, connection: "error" })).toBe(false);
    expect(residentRetrySettled({ onHost: false, connection: "connected" })).toBe(false);
    expect(residentRetrySettled({ onHost: true, connection: "connected" })).toBe(true);
  });
});
