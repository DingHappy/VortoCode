/** Same-origin HttpOnly BFF session. Provider credentials never enter this client. */
import { MobileClientError, mobileTransportOptions, readBootstrap, readSnapshot } from "./index.ts";
import type { ErrorCode, TransportOptions } from "./index.ts";

export type BrowserStatus = { ready: false; signed_in: false; message: string } | {
  ready: true; signed_in: boolean; account_origin: string; discovery_scope: "current_runtime";
  instance_id?: string; name?: string;
};
const fail = (code: ErrorCode = "protocol"): never => { throw new MobileClientError(code); };
const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
};
const text = (value: unknown, max = 4096): string => {
  if (typeof value !== "string" || value.length > max) return fail(); return value;
};

export function createBrowserClient(options: TransportOptions) {
  const { endpoint, transport, timeout } = mobileTransportOptions(options);
  let generation = 0, status: BrowserStatus | null = null;
  async function request(path: "status" | "login" | "logout" | "bootstrap" | "snapshot", signal?: AbortSignal) {
    const owner = generation, controller = new AbortController(); let timedOut = false;
    const abort = () => controller.abort(); if (signal?.aborted) abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => { timedOut = true; abort(); }, timeout);
    const current = () => {
      if (owner !== generation) return fail("credential_changed");
      if (controller.signal.aborted) return fail(timedOut ? "timeout" : "aborted");
    };
    try {
      current();
      const action = path === "login" || path === "logout";
      const route = path === "login" ? "account/login" : `browser/${path}`;
      const response = await transport(`${endpoint}/api/mobile/${route}`, {
        method: action ? "POST" : "GET", credentials: "same-origin", redirect: "error", cache: "no-store",
        headers: { Accept: "application/json" }, signal: controller.signal,
      });
      current();
      if (response.status === 401 || response.status === 403) { status = null; return fail("unauthorized"); }
      if (!response.ok) return fail("unavailable");
      if (!(response.headers.get("content-type") ?? "").includes("application/json")) return fail();
      const body = await response.text(); current(); if (body.length > 1024 * 1024) return fail();
      try { return object(JSON.parse(body)); } catch { return fail(); }
    } catch (error) {
      current(); if (error instanceof MobileClientError) throw error; return fail("unavailable");
    } finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
  }
  function instance() {
    if (!status?.ready || !status.signed_in || !status.instance_id) return fail("unauthorized");
    return status.instance_id;
  }
  return {
    async status(signal?: AbortSignal): Promise<BrowserStatus> {
      generation++; status = null;
      const raw = await request("status", signal);
      if (raw.schema_version !== 1 || typeof raw.ready !== "boolean" || typeof raw.signed_in !== "boolean") return fail();
      if (!raw.ready) {
        if (raw.signed_in) return fail();
        return status = { ready: false, signed_in: false, message: text(raw.message, 300) };
      }
      if (raw.discovery_scope !== "current_runtime") return fail();
      const origin = text(raw.account_origin, 2048);
      mobileTransportOptions({ ...options, origin });
      const value: BrowserStatus = { ready: true, signed_in: raw.signed_in, account_origin: origin, discovery_scope: "current_runtime" };
      if (raw.signed_in) {
        const id = text(raw.instance_id, 32); if (!/^[a-f0-9]{32}$/.test(id)) return fail();
        value.instance_id = id; value.name = text(raw.name, 80);
      }
      return status = value;
    },
    async login(signal?: AbortSignal) {
      if (!status?.ready) return fail("configuration");
      const issuer = status.account_origin;
      generation++; status = null;
      const raw = await request("login", signal);
      let url: URL; try { url = new URL(text(raw.authorize_url)); } catch { return fail(); }
      if (url.origin !== issuer || url.pathname !== "/authorize" || url.username || url.password || url.hash) return fail();
      if (url.searchParams.get("client_id") !== "vortocode-mobile-web" || url.searchParams.get("redirect_uri") !== `${endpoint}/api/mobile/account/callback`) return fail();
      return url.href;
    },
    async bootstrap(signal?: AbortSignal) {
      const expected = instance(); return readBootstrap(await request("bootstrap", signal), expected);
    },
    async snapshot(signal?: AbortSignal) {
      const expected = instance(); return readSnapshot(await request("snapshot", signal), expected);
    },
    async logout(signal?: AbortSignal) {
      generation++; status = null;
      const raw = await request("logout", signal); if (raw.ok !== true) return fail();
    },
    disconnect() { generation++; status = null; },
  };
}
