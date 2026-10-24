import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { errorText } from "./errorText";
import { startPolling } from "./polling";

export interface ResidentDesktopStatus {
  target: string;
  sshConnected: boolean;
  viewerUrl: string | null;
  viewer?: { id: number; surface: "workspace" | "window" } | null;
  computer: { ready: boolean; mode: string; viewers: number; passwordStore: string;
    observerReady: boolean; observers: number | null } | null;
  checkedAt: number | null;
  error: string | null;
}
export interface DesktopSnapshot { status: ResidentDesktopStatus | null; busy: boolean; note: string }
type Call = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

// One shared operation across the settings and daily-work entry. Late status
// replies cannot overwrite an in-flight save/open/close.
export function createResidentDesktopStore(call: Call) {
  let snapshot: DesktopSnapshot = { status: null, busy: false, note: "" };
  let generation = 0;
  let reading: Promise<boolean> | null = null;
  let pendingRefresh = false;
  const listeners = new Set<() => void>();
  const publish = (next: DesktopSnapshot) => { snapshot = next; listeners.forEach(fn => fn()); };
  const drain = () => {
    if (pendingRefresh && !snapshot.busy && !reading) { pendingRefresh = false; void read(false); }
  };
  const read = (poll: boolean): Promise<boolean> => {
    if (snapshot.busy || reading) {
      if (!poll) pendingRefresh = true;
      return reading ?? Promise.resolve(true);
    }
    const request = ++generation;
    const command = poll ? "refresh_resident_desktop" : "get_resident_desktop";
    reading = (async () => {
      try {
        const status = await call<ResidentDesktopStatus>(command);
        if (request === generation) publish({ ...snapshot, status,
          note: snapshot.note === snapshot.status?.error ? "" : snapshot.note });
        return !status.error;
      } catch (error) {
        if (request === generation) {
          const note = errorText(error, "读取远程桌面状态失败");
          publish({ ...snapshot, note, status: snapshot.status ? { ...snapshot.status, error: note } : null });
        }
        return false;
      }
    })().finally(() => { reading = null; drain(); });
    return reading;
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    refresh: () => read(false),
    poll: () => read(true),
    async act(command: "save_resident_desktop_target" | "check_resident_desktop" | "open_resident_desktop" | "open_resident_desktop_workspace" | "close_resident_desktop", args?: Record<string, unknown>) {
      if (snapshot.busy) return;
      ++generation;
      publish({ ...snapshot, busy: true, note: "" });
      try {
        const status = await call<ResidentDesktopStatus>(command, args);
        publish({ status, busy: false, note: command === "save_resident_desktop_target" ? "目标已保存，尚未自动连接" : "" });
        return status;
      } catch (error) {
        const note = errorText(error, "远程桌面操作失败");
        // Retain the native manager's failure/freshness state, not a stale
        // successful connection from before this operation.
        let status = snapshot.status;
        try { status = await call<ResidentDesktopStatus>("get_resident_desktop"); }
        catch { if (status) status = { ...status, sshConnected: false, error: note }; }
        publish({ status, busy: false, note });
      } finally { drain(); }
    },
  };
}

export const residentDesktop = createResidentDesktopStore(invoke);
let subscribers = 0;
let stop: (() => void) | null = null;
let stopPolling: (() => void) | null = null;
let listening = 0;
export function watchResidentDesktop() {
  subscribers++;
  if (subscribers === 1) {
    const request = ++listening;
    void listen("resident-desktop-changed", () => { void residentDesktop.refresh(); }).then(unlisten => {
      if (request !== listening || !subscribers) unlisten(); else stop = unlisten;
    }).catch(() => {});
    void residentDesktop.refresh();
    stopPolling = startPolling(() => residentDesktop.poll(), {
      intervalMs: 5_000, hiddenIntervalMs: 30_000, maxBackoffMs: 30_000, visibility: document,
    });
  }
  return () => {
    if (--subscribers === 0) { ++listening; stop?.(); stop = null; stopPolling?.(); stopPolling = null; }
  };
}

export function desktopConnectionLabel(status: ResidentDesktopStatus | null, now: number): string {
  if (!status?.target) return "尚未设置远程电脑";
  if (status.error) return "连接待检查";
  if (!status.sshConnected) return "未连接";
  if (status.checkedAt === null || now - status.checkedAt < 0 || now - status.checkedAt >= 60_000) return "连接状态待刷新";
  return !status.computer?.ready ? "桌面尚未就绪" : status.computer.observerReady ? "桌面就绪" : "画面入口尚未就绪";
}
