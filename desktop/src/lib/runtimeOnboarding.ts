import type { DesktopProjectProfile } from "../types";
import { isAssistantHost } from "./assistantHost";
import { STORAGE_KEYS } from "./storage";

export type RuntimeSetupRecord = { version: 1; status: "pending"; choice: "local" | "remote" | null }
  | { version: 1; status: "complete"; targetId: string };
type Store = Pick<Storage, "getItem" | "setItem">;
export type RuntimeSetupRead = { record: RuntimeSetupRecord | null; invalid: boolean };
export type RuntimeStartupRoute = { kind: "existing" } | { kind: "restore"; target: DesktopProjectProfile }
  | { kind: "setup"; record: RuntimeSetupRecord; error: string; inspectionFailed?: boolean };

export function readRuntimeSetup(storage: Pick<Store, "getItem">): RuntimeSetupRead {
  try {
    const raw = storage.getItem(STORAGE_KEYS.runtimeSetup);
    if (!raw) return { record: null, invalid: false };
    const value = JSON.parse(raw);
    if (value?.version === 1 && value.status === "pending" && [null, "local", "remote"].includes(value.choice)) {
      return { record: { version: 1, status: "pending", choice: value.choice }, invalid: false };
    }
    if (value?.version === 1 && value.status === "complete" && typeof value.targetId === "string" && value.targetId.trim()) {
      return { record: { version: 1, status: "complete", targetId: value.targetId }, invalid: false };
    }
  } catch { /* Keep the original bytes until an explicit user choice. */ }
  return { record: null, invalid: true };
}

export function writeRuntimeSetup(storage: Pick<Store, "setItem">, record: RuntimeSetupRecord) {
  // Store a projection, never caller-supplied extra fields or credentials.
  const safe: RuntimeSetupRecord = record.status === "complete"
    ? { version: 1, status: "complete", targetId: record.targetId }
    : { version: 1, status: "pending", choice: record.choice };
  storage.setItem(STORAGE_KEYS.runtimeSetup, JSON.stringify(safe));
}

export function hasDesktopHistory(storage: Pick<Store, "getItem">): boolean {
  try { return [STORAGE_KEYS.mainChatSid, STORAGE_KEYS.sid, STORAGE_KEYS.lastProjectId].some(key => Boolean(storage.getItem(key))); }
  catch { return true; } // Unknown old state must not be treated as an empty installation.
}

export function startupRuntimeRoute(read: RuntimeSetupRead, legacy: boolean, profiles: readonly DesktopProjectProfile[], configured: boolean | null, running: boolean): RuntimeStartupRoute {
  const setup = (error = "", record: RuntimeSetupRecord = { version: 1, status: "pending", choice: null }): RuntimeStartupRoute => ({ kind: "setup", record, error });
  if (read.invalid) return setup("安装与连接记录无法读取，请重新选择运行位置；原资料保持原位。");
  if (read.record?.status === "pending") return setup("", read.record);
  if (read.record?.status === "complete") {
    const targetId = read.record.targetId;
    const target = profiles.find(p => p.id === targetId && isAssistantHost(p) && !p.assistantPending);
    return target ? { kind: "restore", target } : setup("上次选择的助手登记无法核对，请重新选择运行位置。");
  }
  if (legacy || profiles.length > 0 || configured || running) return { kind: "existing" };
  if (configured === null) return { kind: "setup", record: { version: 1, status: "pending", choice: null }, error: "无法核对已有模型配置，请重新读取；尚未启动运行端。", inspectionFailed: true };
  return setup();
}

export async function completeRuntimeSetup(profile: DesktopProjectProfile, connect: (profile: DesktopProjectProfile) => Promise<void>, storage: Pick<Store, "setItem">, current: () => boolean): Promise<RuntimeSetupRecord> {
  if (!current() || !isAssistantHost(profile)) throw new Error("连接目标已变化，请重新选择");
  await connect(profile);
  if (!current()) throw new Error("连接目标已变化，未保存引导状态");
  const record: RuntimeSetupRecord = { version: 1, status: "complete", targetId: profile.id };
  try { writeRuntimeSetup(storage, record); }
  catch { throw new Error("助手已连接，但引导进度未能保存。请检查本机存储后重试；原资料仍保留。"); }
  return record;
}
