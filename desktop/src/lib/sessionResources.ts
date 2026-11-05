/** Current-session observations only: this never connects or invokes a tool. */
export type ResourceState = "idle" | "active" | "needs_human" | "cleanup_pending" | "unknown" | "not_attached"
  | "needs_reconciliation" | "loaded" | "not_provided" | "blocked" | "disconnected" | "connecting" | "connected" | "partial" | "error";
export interface SessionResource {
  id: string; kind: "browser" | "device" | "service"; title: string; state: ResourceState;
  approval: "each_action" | "tool_policy" | "unknown";
  target: string; account: string; device: string; detail: string; checked_at: string | null;
}
export interface SessionResources {
  version: 1; session: string; nonce: string; observed_at: string; status: "observed" | "not_loaded";
  truncated: boolean; items: SessionResource[]; attention_id?: string;
  tool_inventory?: ToolInventory;
}
export interface ToolInventory {
  version: 1; truncated: boolean;
  items: { name: string; read_only: boolean | null; required_capabilities: string[] | null }[];
}
const states = new Set(["idle", "active", "needs_human", "cleanup_pending", "unknown", "not_attached",
  "needs_reconciliation", "loaded", "not_provided", "blocked", "disconnected", "connecting", "connected", "partial", "error"]);
const record = (raw: unknown): Record<string, unknown> | null => raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : null;
const text = (v: unknown, max = 240): v is string => typeof v === "string" && v.length <= max && !/[\u0000-\u001f\u007f]/.test(v);
const date = (v: unknown): v is string => text(v, 80) && Number.isFinite(Date.parse(v));

function parseToolInventory(raw: unknown): ToolInventory | undefined {
  const value = record(raw);
  if (!value || value.version !== 1 || typeof value.truncated !== "boolean" || !Array.isArray(value.items) || value.items.length > 256) return;
  const items: ToolInventory["items"] = [], seen = new Set<string>();
  for (const rawItem of value.items) {
    const item = record(rawItem);
    if (!item || !text(item.name, 128) || !/^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$/.test(item.name) || seen.has(item.name)
      || !(item.read_only === null || typeof item.read_only === "boolean")) return;
    const caps = item.required_capabilities;
    if (!(caps === null || (Array.isArray(caps) && caps.length <= 16 && caps.every(cap => typeof cap === "string" && /^[a-z][a-z0-9_:-]{0,79}$/.test(cap))))) return;
    seen.add(item.name); items.push({ name: item.name, read_only: item.read_only, required_capabilities: caps as string[] | null });
  }
  return { version: 1, truncated: value.truncated, items };
}

export function parseSessionResources(raw: unknown): SessionResources | null {
  const r = record(raw);
  if (!r || r.version !== 1 || !text(r.session, 200) || !text(r.nonce, 128) || !date(r.observed_at)
    || !["observed", "not_loaded"].includes(String(r.status)) || typeof r.status !== "string"
    || typeof r.truncated !== "boolean" || !Array.isArray(r.items) || r.items.length > 32
    || (r.attention_id !== undefined && !text(r.attention_id, 128))) return null;
  const seen = new Set<string>();
  const items: SessionResource[] = [];
  for (const value of r.items) {
    const item = record(value);
    if (!item || !text(item.id, 80) || !/^[a-z0-9:_-]+$/.test(item.id) || seen.has(item.id)
      || !text(item.title, 100) || !item.title.trim() || typeof item.kind !== "string" || !["browser", "device", "service"].includes(item.kind)
      || typeof item.state !== "string" || !states.has(item.state) || typeof item.approval !== "string"
      || !["each_action", "tool_policy", "unknown"].includes(item.approval)
      || ![item.target, item.account, item.device, item.detail].every(v => text(v))
      || !(item.checked_at === null || date(item.checked_at))) return null;
    seen.add(item.id);
    items.push({ id: item.id, title: item.title, kind: item.kind as SessionResource["kind"], state: item.state as ResourceState,
      approval: item.approval as SessionResource["approval"], target: item.target as string, account: item.account as string,
      device: item.device as string, detail: item.detail as string, checked_at: item.checked_at as string | null });
  }
  if (r.status === "not_loaded" && items.length) return null;
  return { version: 1, session: r.session, nonce: r.nonce, observed_at: r.observed_at,
    status: r.status as SessionResources["status"], truncated: r.truncated, items,
    ...(r.attention_id ? { attention_id: r.attention_id as string } : {}),
    ...(r.tool_inventory !== undefined && r.status === "observed" ? { tool_inventory: parseToolInventory(r.tool_inventory) } : {}) };
}

export interface ResourceClient { send(event: Record<string, unknown>): Promise<void> }
export interface ResourceSource<C> { client: C | null; session: string; connected: boolean; epoch?: number }
export interface ResourceView { snapshot: SessionResources | null; busy: boolean; stale: boolean; error: string }
export const emptyResourceView = (): ResourceView => ({ snapshot: null, busy: false, stale: true, error: "" });

/** Nonce + exact client/session fence. No retries or polling, including after a timeout. */
export function createResourceReads<C extends ResourceClient>(read: () => ResourceSource<C>,
  publish: (source: ResourceSource<C>, view: ResourceView) => void,
  nonce: () => string = () => globalThis.crypto.randomUUID()) {
  let view = emptyResourceView(), dirty = 0;
  let pending: { source: ResourceSource<C>; nonce: string; dirty: number } | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined, expiry: ReturnType<typeof setTimeout> | undefined;
  let owner: ResourceSource<C> | null = null;
  const same = (source: ResourceSource<C>) => { const now = read(); return now.client === source.client && now.session === source.session && now.epoch === source.epoch; };
  const emit = () => { if (owner && same(owner)) publish(owner, { ...view }); };
  const stop = () => { clearTimeout(timer); clearTimeout(expiry); timer = expiry = undefined; pending = null; };
  const fail = (message: string) => { stop(); view = { ...view, busy: false, stale: true, error: message }; emit(); };
  return {
    reset() { stop(); owner = { ...read() }; view = emptyResourceView(); dirty++; emit(); },
    dispose() { stop(); owner = null; },
    changed() { dirty++; clearTimeout(expiry); view = { ...view, stale: true }; emit(); },
    offline() { fail("连接中断，保留上次记录；当前状态未核实。"); },
    async load() {
      const source = { ...read() };
      if (!source.client || !source.connected) return;
      if (pending && same(pending.source)) return;
      if (!owner || owner.client !== source.client || owner.session !== source.session || owner.epoch !== source.epoch) view = emptyResourceView();
      stop(); owner = source;
      const ticket = { source, nonce: nonce(), dirty }; pending = ticket;
      view = { ...view, busy: true, stale: true, error: "" }; emit();
      timer = setTimeout(() => { if (pending === ticket && same(source)) fail("运行端未返回资源状态；可手动刷新，不会自动重试。"); }, 5000);
      try { await source.client.send({ type: "get_status", resources: ticket.nonce }); }
      catch { if (pending === ticket && same(source)) fail("资源状态请求未确认；不会自动重发。"); }
    },
    accept(raw: unknown) {
      const ticket = pending;
      if (!ticket || record(raw)?.nonce !== ticket.nonce || !same(ticket.source) || !read().connected) return;
      const snapshot = parseSessionResources(raw);
      if (!snapshot || snapshot.session !== `sid-${ticket.source.session}`) { fail("资源状态不兼容或归属不符，未采用。"); return; }
      stop(); view = { snapshot, busy: false, stale: dirty !== ticket.dirty, error: "" }; emit();
      expiry = setTimeout(() => { if (same(ticket.source)) { view = { ...view, stale: true }; emit(); } }, 60_000);
    },
  };
}
