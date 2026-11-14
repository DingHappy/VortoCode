import type { DesktopContext } from "./desktopContext";

/** Only asynchronous setting APIs are exposed; synchronous transport controls are not wrapped. */
type AsyncSettingsClient<T> = {
  [K in keyof T as Exclude<T[K], undefined> extends (...args: never[]) => Promise<unknown> ? K : never]: T[K]
};

export function settingsTargetSummary(target: DesktopContext) {
  const label = target.projectRoot ? `${target.runtimeLabel} · 项目：${target.projectRoot} · 空间归属待核对`
    : `${target.runtimeLabel} · ${target.location}`;
  return target.registrationId ? `${label} · 连接登记：${target.registrationId}` : label;
}

/** UI ownership only. Existing server confirmations, revisions and instance proofs remain authoritative. */
export function settingsTargetLease<T extends object>(source: () => { context: DesktopContext; client: T | null }) {
  const opened = source();
  const epoch = opened.client && Reflect.get(opened.client, "connectionEpoch", opened.client);
  const current = () => {
    const next = source();
    const transport: unknown = opened.client && Reflect.get(opened.client, "desktopSource", opened.client);
    const projectionMatches = transport === undefined || transport !== null && typeof transport === "object"
      && "generation" in transport && transport.generation === opened.context.clientGeneration
      && "epoch" in transport && transport.epoch === opened.context.clientEpoch
      && "sid" in transport && transport.sid === opened.context.sid
      && "registrationId" in transport && transport.registrationId === opened.context.registrationId;
    return opened.context.ready && Boolean(opened.client) && next.context.ready
      && projectionMatches
      && next.context.readKey === opened.context.readKey && next.client === opened.client
      && Reflect.get(opened.client!, "connectionEpoch", opened.client!) === epoch;
  };
  const requireCurrent = () => {
    if (!opened.context.ready || !opened.client) throw new Error("当前目标尚未连接，请连接后重新打开设置");
    if (!current()) throw new Error("设置目标已变化，请关闭后重新打开设置核对；本次操作未重试");
  };
  const run = async <R,>(action: () => Promise<R>): Promise<R> => {
    requireCurrent();
    const result = await action();
    requireCurrent();
    return result;
  };
  const methods = new Map<PropertyKey, unknown>();
  const client = opened.client && new Proxy(opened.client, { get(target, property) {
    const value = Reflect.get(target, property, target);
    if (typeof value !== "function") return value;
    if (!methods.has(property)) methods.set(property, (...args: unknown[]) => run(() => value.apply(target, args)));
    return methods.get(property);
  } }) as AsyncSettingsClient<T> | null;
  return { target: opened.context, client, current, requireCurrent, run };
}

export type SettingsTargetLease<T extends object = object> = ReturnType<typeof settingsTargetLease<T>>;
export type SettingsTargetGuard = Pick<SettingsTargetLease, "current" | "run">;
