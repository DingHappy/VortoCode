import type { TaskItem, DispatchSubmission } from "../types";

/** Retry identity belongs to a particular runtime, session and immutable contract. */
export class DispatchAttempt {
  private attempts = new Map<string, { signature: string; requestId: string }>();

  prepare(scope: string, input: Omit<DispatchSubmission, "request_id">, newId: () => string = () => crypto.randomUUID()): DispatchSubmission {
    const signature = JSON.stringify(input);
    let attempt = this.attempts.get(scope);
    if (signature !== attempt?.signature) {
      attempt = { signature, requestId: newId() };
      this.attempts.set(scope, attempt);
    }
    return { ...input, request_id: attempt!.requestId };
  }

  complete(scope: string): void {
    this.attempts.delete(scope);
  }
}

export function dispatchTaskIdentity(task: TaskItem): { session: string; round: number } | null {
  if (task.kind !== "delegation" || task.collaboration?.dispatch?.source !== "api"
      || !/^task-[A-Za-z0-9_-]+$/.test(task.id) || !task.owner_session?.startsWith("sid-")) return null;
  const session = task.owner_session.slice(4);
  const round = task.collaboration.round;
  if (!/^[A-Za-z0-9._-]{1,120}$/.test(session) || !Number.isInteger(round) || round < 1 || round > 3) return null;
  return { session, round };
}

export function dependencyCandidates(tasks: TaskItem[], session: string) {
  return tasks.flatMap((task) => {
    const owner = dispatchTaskIdentity(task);
    return owner?.session === session && ["waiting", "queued", "running", "blocked", "done"].includes(task.status)
      ? [{ task_id: task.id, round: owner.round, prompt: task.prompt || task.id, status: task.status }] : [];
  });
}

export function dependencySelectionCurrent(refs: Array<{ task_id: string; round: number }>, tasks: TaskItem[], session: string) {
  const candidates = dependencyCandidates(tasks, session);
  return refs.length <= 8 && new Set(refs.map((ref) => ref.task_id)).size === refs.length
    && refs.every((ref) => candidates.some((task) => task.task_id === ref.task_id && task.round === ref.round));
}

export function chainBudgetSelection(refs: Array<{ task_id: string; round: number }>, tasks: TaskItem[], session: string) {
  const roots = new Set<string>();
  let invalid = false;
  for (const ref of refs) {
    const task = tasks.find((item) => item.id === ref.task_id);
    const owner = task && dispatchTaskIdentity(task);
    if (!task || owner?.session !== session || owner.round !== ref.round) { invalid = true; continue; }
    const budget = task.chain_budget;
    if (!budget || Object.keys(budget).length === 0) continue;
    if (budget.available !== true || budget.mode !== "execution_allowance" || !/^task-[A-Za-z0-9_-]+$/.test(budget.root_task_id || "")) {
      invalid = true; continue;
    }
    roots.add(budget.root_task_id!);
  }
  return { roots: [...roots].sort(), invalid };
}
