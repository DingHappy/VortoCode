import { invoke } from "@tauri-apps/api/core";

export interface ResidentAccount {
  projectId: string; name: string; baseUrl: string; instanceId: string | null; revision: number;
  registered: boolean; belongsToCurrent: boolean | null; mobileUrl: string | null;
}
export interface ResidentAccountState { busy: boolean; value: ResidentAccount | null; error: string }
const fail = () => { throw new Error("助手账号状态无法核验，请刷新后重试"); };
function secureUrl(raw: string, path: string): boolean {
  try {
    const url = new URL(raw);
    return !url.username && !url.password && !url.search && !url.hash && url.pathname === path
      && (url.protocol === "https:" || url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname));
  } catch { return false; }
}
export function parseResidentAccount(raw: unknown, projectId: string): ResidentAccount {
  if (!raw || typeof raw !== "object") return fail();
  const value = raw as ResidentAccount;
  if (value.projectId !== projectId || typeof value.name !== "string" || value.name.length > 200
      || typeof value.baseUrl !== "string" || !secureUrl(value.baseUrl, "/") || !Number.isSafeInteger(value.revision) || value.revision < 0
      || !(value.instanceId === null || typeof value.instanceId === "string" && /^[a-f0-9]{32}$/.test(value.instanceId))
      || typeof value.registered !== "boolean" || ![null, true, false].includes(value.belongsToCurrent)
      || (value.registered && (!value.instanceId || value.revision < 1))
      || !(value.mobileUrl === null || typeof value.mobileUrl === "string" && secureUrl(value.mobileUrl, "/mobile"))) return fail();
  // Only display fields cross into the UI. Native verifies saved destinations and keeps all proofs.
  return { projectId, name: value.name, baseUrl: value.baseUrl, instanceId: value.instanceId, revision: value.revision,
    registered: value.registered, belongsToCurrent: value.belongsToCurrent, mobileUrl: value.mobileUrl };
}
export const readResidentAccount = (projectId: string) => invoke<unknown>("resident_account_status", { projectId });
export const updateResidentAccount = (projectId: string, value: ResidentAccount, bind: boolean) =>
  invoke<unknown>("resident_account_update", { projectId, instanceId: value.instanceId, revision: value.revision, bind });

/** One card owns its target and results; an account change invalidates even an in-flight mutation. */
export function residentAccountController(projectId: string, ports: {
  read: () => Promise<unknown>; update: (value: ResidentAccount, bind: boolean) => Promise<unknown>;
  publish: (state: ResidentAccountState) => void; error: (error: unknown) => string;
}) {
  let generation = 0, disposed = false, pending = false, value: ResidentAccount | null = null;
  const publish = (busy: boolean, error = "") => { if (!disposed) ports.publish({ busy, value, error }); };
  async function run(bind?: boolean) {
    if (disposed || pending || (bind !== undefined && !value)) return;
    const owner = ++generation; pending = true; const current = value;
    if (bind === undefined) value = null;
    publish(true);
    try {
      const raw = bind === undefined ? await ports.read() : await ports.update(current!, bind);
      if (disposed || generation !== owner) return;
      value = parseResidentAccount(raw, projectId); publish(false);
    } catch (error) {
      if (disposed || generation !== owner) return;
      value = null; publish(false, ports.error(error)); // Lost write receipts require a fresh read, never a write retry.
    } finally { if (generation === owner) pending = false; }
  }
  return {
    refresh: () => run(), update: (bind: boolean) => run(bind),
    invalidate() { generation++; pending = false; value = null; publish(false); },
    dispose() { disposed = true; generation++; value = null; },
  };
}
