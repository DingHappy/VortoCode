import type { TaskItem } from "../types";
import { dispatchTaskIdentity } from "./taskDispatch";

/** Only act on a server-provided unread delivery and its exact owner/version. */
export function taskHandoffIdentity(task: TaskItem): { session: string; revision: string } | null {
  const handling = task.handoff?.handling;
  if (!handling || handling.handled !== false || handling.revision?.length !== 64 || !/^[a-f0-9]{64}$/.test(handling.revision)
      || !/^task-[A-Za-z0-9_-]+$/.test(task.id) || task.id.trim() !== task.id || !task.owner_session?.startsWith("sid-")
      || !["done", "failed", "cancelled", "paused", "interrupted"].includes(task.status)
      || (task.kind !== "dev" && task.kind !== "dev-resume" && !dispatchTaskIdentity(task))) return null;
  const session = task.owner_session.slice(4);
  return session.trim() === session && /^[A-Za-z0-9._-]{1,120}$/.test(session) ? { session, revision: handling.revision } : null;
}
