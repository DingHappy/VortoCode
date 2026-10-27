/** Mobile read transport. No admin token, storage, write, retry or model API. */
export type ErrorCode = "configuration" | "unauthorized" | "unavailable" | "protocol" | "wrong_instance" | "credential_changed" | "aborted" | "timeout";
export class MobileClientError extends Error {
  readonly code: ErrorCode;
  constructor(code: ErrorCode) { super(`Mobile read failed: ${code}`); this.name = "MobileClientError"; this.code = code; }
}
export interface Device {
  id: string; name: string; created_at: number; expires_at: number; revoked_at: number | null; scopes: string[];
}
export interface Bootstrap {
  schema_version: 1; instance_id: string; device: Device;
  capabilities: { reads: string[]; writes: readonly [] };
}
export interface GoalSummary {
  id: string; objective: string; status: string; next_action: string; blocker: string; revision: number;
}
/** unavailable + retained data is historical, never a current observation. */
export interface Source<T> { status: "current" | "unavailable"; checked_at: string | null; data: T | null }
export interface Snapshot {
  schema_version: 1; instance_id: string;
  sources: { profile: Source<{ name: string }>; main_chat: Source<{ sid: string | null }>;
    goals: Source<{ items: GoalSummary[]; truncated: boolean }> };
}

const fail = (code: ErrorCode = "protocol"): never => { throw new MobileClientError(code); };
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function text(value: unknown, maximum = 4096): string {
  if (typeof value !== "string" || value.length > maximum) return fail();
  return value;
}
function id(value: unknown): string {
  const result = text(value, 32); if (!/^[a-f0-9]{32}$/.test(result)) return fail(); return result;
}
function integer(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) return fail(); return value;
}
function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 100) return fail(); return value.map(item => text(item, 80));
}
function envelope(value: unknown, expected: string) {
  const raw = object(value);
  if (raw.schema_version !== 1) return fail();
  if (id(raw.instance_id) !== expected) return fail("wrong_instance");
  return raw;
}
export function readBootstrap(value: unknown, expected: string): Bootstrap {
  const raw = envelope(value, expected), device = object(raw.device), capabilities = object(raw.capabilities);
  const created_at = integer(device.created_at), expires_at = integer(device.expires_at);
  if (expires_at <= created_at || device.revoked_at !== null) return fail();
  const scopes = strings(device.scopes); if (!scopes.includes("assistant:read")) return fail();
  strings(capabilities.writes); // Validate but never enable future write capabilities.
  return { schema_version: 1, instance_id: expected,
    device: { id: id(device.id), name: text(device.name, 80), created_at, expires_at, revoked_at: null, scopes },
    capabilities: { reads: strings(capabilities.reads), writes: [] } };
}
function source<T>(value: unknown, parse: (data: unknown) => T): Source<T> {
  const raw = object(value);
  if (raw.status === "unavailable") {
    if (raw.data !== null || raw.checked_at !== null) return fail();
    return { status: "unavailable", data: null, checked_at: null };
  }
  if (raw.status !== "current") return fail();
  const checked_at = text(raw.checked_at, 64);
  if (!/^\d{4}-\d\d-\d\dT/.test(checked_at) || !Number.isFinite(Date.parse(checked_at))) return fail();
  return { status: "current", checked_at, data: parse(raw.data) };
}
export function readSnapshot(value: unknown, expected: string): Snapshot {
  const raw = envelope(value, expected), sources = object(raw.sources);
  return { schema_version: 1, instance_id: expected, sources: {
    profile: source(sources.profile, value => ({ name: text(object(value).name, 80) })),
    main_chat: source(sources.main_chat, value => {
      const sid = object(value).sid;
      if (sid === null) return { sid: null };
      const result = text(sid, 80); if (!/^[A-Za-z0-9_-]{1,80}$/.test(result)) return fail(); return { sid: result };
    }),
    goals: source(sources.goals, value => {
      const data = object(value);
      if (!Array.isArray(data.items) || data.items.length > 100 || typeof data.truncated !== "boolean") return fail();
      const seen = new Set<string>();
      const items = data.items.map(value => {
        const item = object(value), goalId = text(item.id, 80), revision = integer(item.revision);
        if (!/^personal-[A-Za-z0-9_-]{1,64}$/.test(goalId) || seen.has(goalId) || revision < 1) return fail();
        seen.add(goalId);
        return { id: goalId, objective: text(item.objective), status: text(item.status, 80),
          next_action: text(item.next_action), blocker: text(item.blocker), revision };
      });
      return { items, truncated: data.truncated };
    }),
  } };
}

export interface Options {
  origin: string;
  instanceId: string; // From the trusted provisioning result, not the first arbitrary response.
  credential: () => string | null; // Native clients supply secure storage; never localStorage or URLs.
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
  allowInsecureLoopback?: boolean;
}
export type TransportOptions = Pick<Options, "origin" | "allowInsecureLoopback" | "fetch" | "timeoutMs">;
export function mobileTransportOptions(options: TransportOptions) {
  let base: URL;
  try { base = new URL(options.origin); } catch { return fail("configuration"); }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(base.hostname);
  if (base.username || base.password || base.search || base.hash || base.pathname !== "/"
      || (base.protocol !== "https:" && !(base.protocol === "http:" && loopback && options.allowInsecureLoopback))) return fail("configuration");
  const timeout = options.timeoutMs ?? 10000;
  if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 60000) return fail("configuration");
  const endpoint = base.origin, transport = options.fetch ?? globalThis.fetch;
  if (typeof transport !== "function") return fail("configuration");
  return { endpoint, transport, timeout };
}
export function createMobileClient(options: Options) {
  const { endpoint, transport, timeout } = mobileTransportOptions(options);
  if (!/^[a-f0-9]{32}$/.test(options.instanceId)) return fail("configuration");
  const instance = options.instanceId;
  const credential = () => {
    try { return options.credential(); } catch { return fail("unauthorized"); }
  };
  async function request<T>(path: "bootstrap" | "snapshot", parse: (value: unknown, expected: string) => T, signal?: AbortSignal): Promise<T> {
    const token = credential();
    if (!token || !/^vcm_[A-Za-z0-9_-]{43}$/.test(token)) return fail("unauthorized");
    const controller = new AbortController();
    let timedOut = false;
    const abort = () => controller.abort();
    if (signal?.aborted) abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => { timedOut = true; abort(); }, timeout);
    const current = () => {
      if (credential() !== token) return fail("credential_changed");
      if (controller.signal.aborted) return fail(timedOut ? "timeout" : "aborted");
    };
    try {
      current();
      const response = await transport(`${endpoint}/api/mobile/v1/${path}`, {
        method: "GET", credentials: "omit", cache: "no-store", redirect: "error",
        headers: { Authorization: `Bearer ${token}`, Accept: "application/json" }, signal: controller.signal,
      });
      current();
      if (response.status === 401 || response.status === 403) return fail("unauthorized");
      if (!response.ok) return fail("unavailable");
      if (!(response.headers.get("content-type") ?? "").includes("application/json")) return fail();
      const body = await response.text();
      current();
      if (body.length > 1024 * 1024) return fail();
      let value: unknown;
      try { value = JSON.parse(body); } catch { return fail(); }
      return parse(value, instance);
    } catch (error) {
      current();
      if (error instanceof MobileClientError) throw error;
      return fail("unavailable"); // Never echo response bodies, URLs or transport errors containing secrets.
    } finally {
      clearTimeout(timer); signal?.removeEventListener("abort", abort);
    }
  }
  return {
    bootstrap: (signal?: AbortSignal) => request("bootstrap", readBootstrap, signal),
    snapshot: (signal?: AbortSignal) => request("snapshot", readSnapshot, signal),
  };
}

export interface ReadState { status: "idle" | "loading" | "current" | "unavailable"; snapshot: Snapshot | null }
/** Latest-request ownership prevents late results from overwriting a new read or disconnect. */
export function createMobileReader(client: Pick<ReturnType<typeof createMobileClient>, "snapshot">, update: (state: ReadState) => void) {
  let generation = 0, controller: AbortController | null = null;
  let state: ReadState = { status: "idle", snapshot: null };
  const publish = (next: ReadState) => { state = next; update(next); };
  const historical = (snapshot: Snapshot | null): Snapshot | null => snapshot && ({ ...snapshot,
    sources: { profile: { ...snapshot.sources.profile, status: "unavailable" },
      main_chat: { ...snapshot.sources.main_chat, status: "unavailable" },
      goals: { ...snapshot.sources.goals, status: "unavailable" } } });
  return {
    async refresh() {
      const owner = ++generation;
      controller?.abort(); const request = new AbortController(); controller = request;
      publish({ status: "loading", snapshot: historical(state.snapshot) });
      try {
        const snapshot = await client.snapshot(request.signal);
        if (owner !== generation) return;
        const previous = state.snapshot;
        for (const key of ["profile", "main_chat", "goals"] as const) {
          if (snapshot.sources[key].status === "unavailable" && previous?.instance_id === snapshot.instance_id) {
            // Retain only that source's old observation, explicitly marked unavailable.
            Object.assign(snapshot.sources[key], previous.sources[key], { status: "unavailable" });
          }
        }
        publish({ status: Object.values(snapshot.sources).some(source => source.status === "current") ? "current" : "unavailable", snapshot });
      } catch (error) {
        if (owner !== generation) return;
        const reset = error instanceof MobileClientError && ["credential_changed", "wrong_instance", "unauthorized"].includes(error.code);
        publish({ status: "unavailable", snapshot: reset ? null : historical(state.snapshot) });
      } finally { if (owner === generation) controller = null; }
    },
    disconnect() {
      generation++; controller?.abort(); controller = null;
      publish({ status: "idle", snapshot: null });
    },
  };
}
