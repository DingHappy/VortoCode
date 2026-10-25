/** Read-only v1 projection. Results, evidence and features grant no UI authority. */
export interface ExecutionObservation {
  schema_version: 1;
  ref: { backend_id: string; instance_id: string; run_id: string;
    session_id: string; turn_id: string; task_id: string; scope: "process" | "runtime" };
  state: "queued" | "running" | "waiting_user" | "finished" | "failed" | "cancelled" | "unknown";
  observed_at: number;
  native_state: string;
  progress: { summary: string; completed_steps?: number | null; total_steps?: number | null };
}
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const counter = (value: unknown) => value == null || (Number.isSafeInteger(value) && (value as number) >= 0);

/** Decode the fields used by the workday, ignoring additive and unused fields. */
export function readExecutionObservation(value: unknown): ExecutionObservation | null {
  if (!object(value) || value.schema_version !== 1 || !object(value.ref)) return null;
  const ref = value.ref;
  if (["backend_id", "instance_id", "run_id"].some(key => typeof ref[key] !== "string" || !(ref[key] as string).trim())
      || ["session_id", "turn_id", "task_id"].some(key => ref[key] !== undefined && typeof ref[key] !== "string")
      || (ref.scope !== undefined && ref.scope !== "process" && ref.scope !== "runtime")
      || typeof value.state !== "string" || typeof value.observed_at !== "number"
      || !Number.isFinite(value.observed_at) || value.observed_at < 0
      || (value.native_state !== undefined && typeof value.native_state !== "string")) return null;
  const progress = value.progress === undefined ? {} : value.progress;
  if (!object(progress) || (progress.summary !== undefined && typeof progress.summary !== "string")
      || !counter(progress.completed_steps) || !counter(progress.total_steps)
      || (typeof progress.completed_steps === "number" && typeof progress.total_steps === "number"
        && progress.completed_steps > progress.total_steps)) return null;
  const states = ["queued", "running", "waiting_user", "finished", "failed", "cancelled", "unknown"];
  const knownState = states.includes(value.state);
  const state = knownState ? value.state as ExecutionObservation["state"] : "unknown";
  return {
    schema_version: 1,
    ref: { backend_id: ref.backend_id as string, instance_id: ref.instance_id as string, run_id: ref.run_id as string,
      session_id: (ref.session_id ?? "") as string, turn_id: (ref.turn_id ?? "") as string,
      task_id: (ref.task_id ?? "") as string, scope: (ref.scope ?? "process") as "process" | "runtime" },
    state, observed_at: value.observed_at,
    native_state: (value.native_state as string) || (!knownState ? value.state : ""),
    progress: { summary: (progress.summary ?? "") as string,
      completed_steps: progress.completed_steps as number | null | undefined,
      total_steps: progress.total_steps as number | null | undefined },
  };
}
