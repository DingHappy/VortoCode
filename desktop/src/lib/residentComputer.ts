/** Metadata only. The serving runtime observes its own desktop; no frames cross this API. */
export interface ResidentComputer {
  schema_version: 1;
  execution_location: "runtime_host";
  state: "ready" | "not_ready" | "not_installed" | "unavailable";
  control_mode: "human" | "agent" | "unknown";
  viewers: number | null;
  password_store: "needs_setup" | "locked" | "unlocked" | "unavailable" | "unknown";
  observation: { available: boolean; viewers: number } | null;
  resident_worker: { connected: false; unattended: false };
}

export interface ComputerRead {
  status: "loading" | "current" | "unavailable" | "unsupported";
  data?: ResidentComputer;
  checkedAt?: number;
}

export function readResidentComputer(value: unknown): ResidentComputer | null {
  if (value === undefined || value === null) return null; // older server
  if (typeof value !== "object") throw new Error("Invalid computer status");
  const raw = value as Record<string, unknown>;
  const worker = raw.resident_worker as Record<string, unknown> | undefined;
  if (raw.schema_version !== 1 || raw.execution_location !== "runtime_host"
      || !["ready", "not_ready", "not_installed", "unavailable"].includes(String(raw.state))
      || !["human", "agent", "unknown"].includes(String(raw.control_mode))
      || !["needs_setup", "locked", "unlocked", "unavailable", "unknown"].includes(String(raw.password_store))
      || (raw.viewers !== null && (typeof raw.viewers !== "number" || !Number.isInteger(raw.viewers) || raw.viewers < 0 || raw.viewers > 32))
      || !worker || worker.connected !== false || worker.unattended !== false) throw new Error("Invalid computer status");
  // Explicit projection: arbitrary strings, media, credentials and future
  // permission claims cannot flow into this version of the controller UI.
  const observer = raw.observation as Record<string, unknown> | undefined;
  const observation = observer && typeof observer.available === "boolean" && typeof observer.viewers === "number"
    && Number.isInteger(observer.viewers) && observer.viewers >= 0 && observer.viewers <= 32
    ? { available: observer.available, viewers: observer.viewers } : null;
  return { schema_version: 1, execution_location: "runtime_host", state: raw.state as ResidentComputer["state"],
    control_mode: raw.control_mode as ResidentComputer["control_mode"], viewers: raw.viewers as number | null,
    password_store: raw.password_store as ResidentComputer["password_store"], observation, resident_worker: { connected: false, unattended: false } };
}

export function residentComputerReads(deps: {
  read: () => Promise<unknown>; current: () => boolean; publish: (source: ComputerRead) => void; now?: () => number;
}) {
  let pending: Promise<void> | null = null;
  let source: ComputerRead = { status: "loading" };
  const refresh = () => {
    if (!deps.current()) return Promise.resolve();
    if (pending) return pending;
    deps.publish(source = { ...source, status: "loading" });
    const request = Promise.resolve().then(deps.read).then(value => {
      const data = readResidentComputer(value);
      if (deps.current()) deps.publish(source = data
        ? { status: "current", data, checkedAt: (deps.now ?? Date.now)() } : { status: "unsupported" });
    }).catch(() => { if (deps.current()) deps.publish(source = { ...source, status: "unavailable" }); })
      .finally(() => { if (pending === request) pending = null; });
    pending = request;
    return request;
  };
  return { refresh };
}
