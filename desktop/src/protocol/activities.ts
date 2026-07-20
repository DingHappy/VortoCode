// App.tsx 抽出的协议活动映射族（B8-④b S1，只搬家不改行为）。
// 把 ProtocolEvent 流（agent_phase / agent_tool / agent_hook）折叠成 TurnActivity 时间线，
// 纯函数、无副作用；事件总线（handleProtocolEvent）仍留在 App.tsx，只是调用这些映射器。
import type { ProtocolEvent, TurnActivity } from "../types";

export function protocolActivity(event: ProtocolEvent): TurnActivity | null {
  if (!event.id) return null;
  if (event.type === "agent_phase") {
    return {
      id: event.id,
      rid: event.rid,
      kind: "phase",
      status: String(event.status ?? "running"),
      label: String(event.label ?? "正在工作"),
      phase: String(event.phase ?? "working"),
      durationMs: typeof event.duration_ms === "number" ? event.duration_ms : undefined,
    };
  }
  if (event.type === "agent_tool") {
    return {
      id: event.id,
      rid: event.rid,
      kind: "tool",
      status: String(event.status ?? "running"),
      label: String(event.summary ?? event.name ?? "工具调用"),
      name: String(event.name ?? "tool"),
      args: event.args,
      result: event.result == null ? undefined : String(event.result),
      durationMs: typeof event.duration_ms === "number" ? event.duration_ms : undefined,
    };
  }
  if (event.type === "agent_hook") {
    return {
      id: event.id,
      rid: event.rid,
      kind: "hook",
      status: String(event.status ?? "running"),
      label: String(event.summary ?? event.name ?? "Hook"),
      name: String(event.name ?? "hook"),
      event: event.event == null ? undefined : String(event.event),
      tool: event.tool == null ? undefined : String(event.tool),
      result: event.message == null ? undefined : String(event.message),
      error: event.error == null ? undefined : String(event.error),
      durationMs: typeof event.duration_ms === "number" ? event.duration_ms : undefined,
    };
  }
  return null;
}

export function upsertActivity(previous: TurnActivity[], activity: TurnActivity): TurnActivity[] {
  const index = previous.findIndex((item) => item.id === activity.id);
  if (index < 0) return [...previous.slice(-299), activity];
  const next = [...previous];
  next[index] = { ...previous[index], ...activity };
  return next;
}

export function hydrateActivities(items: unknown[]): TurnActivity[] {
  return items.reduce<TurnActivity[]>((previous, item) => {
    const activity = protocolActivity((item ?? {}) as ProtocolEvent);
    return activity ? upsertActivity(previous, activity) : previous;
  }, []);
}

export function finishRunningActivities(
  previous: TurnActivity[],
  rid: string | undefined,
  status: "completed" | "failed" | "cancelled",
): TurnActivity[] {
  return previous.map((activity) => {
    if (activity.status !== "running" || (rid && activity.rid !== rid)) return activity;
    const label = status === "completed"
      ? activity.label.replace(/^正在/, "已")
      : status === "cancelled"
        ? `${activity.kind === "tool" ? "工具调用" : activity.kind === "hook" ? "Hook" : "任务"}已取消`
        : `${activity.kind === "tool" ? "工具调用" : activity.kind === "hook" ? "Hook" : "任务"}失败`;
    return { ...activity, status, label };
  });
}
