/** Credential proof and user-visible code; no admin token or plaintext persistence. */
import { createMobileClient, mobileTransportOptions, MobileClientError, readBootstrap } from "./index.ts";
import type { Device } from "./index.ts";

export interface Pairing {
  schema_version: 1; instance_id: string; id: string; name: string; code: string; expires_at: number;
  status: "pending" | "approved" | "rejected" | "expired" | "revoked";
  scopes: string[]; device: Device | null;
}
export interface PairingOptions {
  origin: string; allowInsecureLoopback?: boolean; fetch?: typeof globalThis.fetch;
  crypto?: Pick<Crypto, "getRandomValues">; timeoutMs?: number;
}
function invalid(): never { throw new MobileClientError("protocol"); }
function parse(value: unknown, requestId: string, instance: string | null): Pairing {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const raw = value as Record<string, unknown>;
  if (raw.schema_version !== 1 || raw.id !== requestId || typeof raw.instance_id !== "string"
      || !/^[a-f0-9]{32}$/.test(raw.instance_id) || typeof raw.name !== "string" || raw.name.length > 80
      || typeof raw.code !== "string" || !/^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/.test(raw.code)
      || typeof raw.expires_at !== "number" || !Number.isSafeInteger(raw.expires_at) || raw.expires_at < 0
      || !["pending", "approved", "rejected", "expired", "revoked"].includes(String(raw.status))
      || !Array.isArray(raw.scopes) || !raw.scopes.includes("assistant:read") || raw.scopes.some(scope => typeof scope !== "string")) return invalid();
  if (instance !== null && raw.instance_id !== instance) throw new MobileClientError("wrong_instance");
  let device: Device | null = null;
  if (raw.status === "approved") {
    device = readBootstrap({ schema_version: 1, instance_id: raw.instance_id, device: raw.device,
      capabilities: { reads: [], writes: [] } }, raw.instance_id).device;
    if (device.id !== requestId) return invalid();
  } else if (raw.device !== null) return invalid();
  return { schema_version: 1, instance_id: raw.instance_id, id: requestId, name: raw.name, code: raw.code,
    expires_at: raw.expires_at, status: raw.status as Pairing["status"], scopes: ["assistant:read"], device };
}

export function createPairingSession(options: PairingOptions, name: string) {
  const { endpoint: origin, transport, timeout } = mobileTransportOptions(options);
  if (!name.trim() || name.length > 80) throw new MobileClientError("configuration");
  const random = options.crypto ?? globalThis.crypto;
  if (!random?.getRandomValues) throw new MobileClientError("configuration");
  const requestId = Array.from(random.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, "0")).join("");
  const bytes = random.getRandomValues(new Uint8Array(32));
  const token = "vcm_" + btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  let instance: string | null = null;
  async function request(action: "start" | "status" | "cancel", signal?: AbortSignal): Promise<Pairing> {
    const controller = new AbortController(); let timedOut = false;
    const abort = () => controller.abort(); if (signal?.aborted) abort(); signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => { timedOut = true; abort(); }, timeout);
    const check = () => { if (controller.signal.aborted) throw new MobileClientError(timedOut ? "timeout" : "aborted"); };
    try {
      check();
      const response = await transport(`${origin}/api/mobile/pairing/${action}`, {
        method: "POST", credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal,
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ request_id: requestId, device_token: token, ...(action === "start" ? { name: name.trim() } : {}) }),
      });
      check();
      if (!response.ok) throw new MobileClientError(response.status === 401 || response.status === 403 ? "unauthorized" : "unavailable");
      if (!(response.headers.get("content-type") ?? "").includes("application/json")) return invalid();
      const body = await response.text(); check(); if (body.length > 16384) return invalid();
      let raw: unknown; try { raw = JSON.parse(body); } catch { return invalid(); }
      const result = parse(raw, requestId, instance);
      if (action === "start") instance = result.instance_id;
      return result;
    } catch (error) {
      check(); if (error instanceof MobileClientError) throw error;
      throw new MobileClientError("unavailable");
    } finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
  }
  return {
    start: (signal?: AbortSignal) => request("start", signal),
    status: (signal?: AbortSignal) => request("status", signal),
    cancel: (signal?: AbortSignal) => request("cancel", signal),
    async connect(signal?: AbortSignal) {
      const result = await request("status", signal);
      if (result.status !== "approved" || !instance) throw new MobileClientError("unauthorized");
      const client = createMobileClient({ ...options, origin, instanceId: instance, credential: () => token });
      const bootstrap = await client.bootstrap(signal);
      if (bootstrap.device.id !== requestId) return invalid();
      return client; // Credential stays in this closure for the lifetime of the page/session.
    },
  };
}
