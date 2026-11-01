import { invoke } from "@tauri-apps/api/core";
import { isModelProfileConflict } from "./modelProfileConflict";

export interface CredentialStorage {
  version: 1; mode: "legacy" | "keychain" | "none"; pendingCleanup: number;
}
export interface CredentialStorageRead { busy: boolean; value: CredentialStorage | null; error: string }
export type CredentialStorageAction = "migrate" | "cleanup";

export function parseCredentialStorage(raw: unknown): CredentialStorage {
  if (!raw || typeof raw !== "object") throw new Error("凭证存储状态无法核验，请刷新");
  const value = raw as CredentialStorage;
  if (value.version !== 1 || !["legacy", "keychain", "none"].includes(value.mode)
      || !Number.isSafeInteger(value.pendingCleanup) || value.pendingCleanup < 0 || value.pendingCleanup > 32) {
    throw new Error("凭证存储状态无法核验，请刷新");
  }
  return { version: 1, mode: value.mode, pendingCleanup: value.pendingCleanup };
}

export const readCredentialStorage = () => invoke<unknown>("get_model_credential_storage");
export const updateCredentialStorage = (action: CredentialStorageAction, expectedRevision: string) =>
  invoke<unknown>("update_model_credential_storage", { action, expectedRevision });

export function credentialStorageController(revision: string | undefined, ports: {
  read: () => Promise<unknown>; update: (action: CredentialStorageAction, revision: string) => Promise<unknown>;
  publish: (state: CredentialStorageRead) => void; error: (error: unknown) => string;
}) {
  let disposed = false, pending = false, revisionConflict = false, value: CredentialStorage | null = null;
  const conflictMessage = "模型配置已变化，请先在模型设置中重新载入最新配置，再返回此处。当前草稿不会自动覆盖。";
  const publish = (busy: boolean, error = "") => { if (!disposed) ports.publish({ busy, value, error }); };
  async function run(action?: CredentialStorageAction) {
    if (disposed || pending || action && (revisionConflict || !revision || !value
        || action === "migrate" && value.mode !== "legacy"
        || action === "cleanup" && value.pendingCleanup === 0)) return;
    pending = true;
    if (!action) value = null;
    publish(true);
    try {
      const raw = action ? await ports.update(action, revision!) : await ports.read();
      if (disposed) return;
      value = parseCredentialStorage(raw); publish(false, revisionConflict ? conflictMessage : "");
    } catch (error) {
      if (disposed) return;
      revisionConflict = revisionConflict || isModelProfileConflict(error);
      value = null; publish(false, revisionConflict ? conflictMessage : ports.error(error)); // Lost receipts require a read, never an automatic write retry.
    } finally { pending = false; }
  }
  return { refresh: () => run(), update: (action: CredentialStorageAction) => run(action),
    dispose() { disposed = true; value = null; } };
}
