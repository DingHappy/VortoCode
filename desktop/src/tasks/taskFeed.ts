import type { GatewayClient } from "../gateway";
import type { ProtocolEvent, TaskItem, WorktreeWorkspaceSnapshot } from "../types";

export type TaskClient = Pick<GatewayClient,
  "listTasks" | "getWorktreeWorkspace" | "cancelTask" | "pauseTask" | "resumeTask">;
export type TaskFeedSnapshot = {
  tasks: TaskItem[];
  focusedTaskId: string;
  worktreeWorkspace: WorktreeWorkspaceSnapshot;
};

const emptySnapshot = (): TaskFeedSnapshot => ({
  tasks: [], focusedTaskId: "", worktreeWorkspace: { worktrees: [], plans: [] },
});

/** One hook-owned mirror of server snapshots; no status or permission inference. */
export class TaskFeed {
  private snapshot = emptySnapshot();
  private listeners = new Set<() => void>();
  private epoch = 0;
  private taskRevision = 0;
  private taskRequest = 0;
  private worktreeRequest = 0;
  private eventRefresh: { client: TaskClient; epoch: number; dirty: boolean } | null = null;

  constructor(private readonly getClient: () => TaskClient | null) {}

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  private replace(next: TaskFeedSnapshot) {
    this.snapshot = next;
    this.listeners.forEach((listener) => listener());
  }

  /** Capture both connection identity and reset generation before an await. */
  capture = () => {
    const client = this.getClient();
    const epoch = this.epoch;
    return { client, current: () => client !== null && this.getClient() === client && this.epoch === epoch };
  };

  reset = () => {
    this.epoch += 1;
    this.taskRevision += 1;
    this.eventRefresh = null;
    this.replace(emptySnapshot());
  };

  focus = (focusedTaskId: string) => {
    this.replace({ ...this.snapshot, focusedTaskId });
  };

  upsert = (task: TaskItem, source: TaskClient | null = this.getClient()) => {
    if (!task?.id || !source || source !== this.getClient()) return false;
    if (this.eventRefresh?.client === source && this.eventRefresh.epoch === this.epoch) {
      this.eventRefresh.dirty = true;
    }
    this.taskRevision += 1;
    this.replace({ ...this.snapshot, tasks: [task, ...this.snapshot.tasks.filter((item) => item.id !== task.id)] });
    return true;
  };

  acceptEvent = (event: ProtocolEvent, source: TaskClient | null) => {
    if (!source || source !== this.getClient()) return false;
    if (event.type === "task_snapshot") {
      this.taskRevision += 1;
      // This event already supplies all projections; discard any queued follow-up.
      this.eventRefresh = null;
      this.replace({ ...this.snapshot, tasks: Array.isArray(event.data) ? event.data as TaskItem[] : [] });
      return true;
    }
    if (event.type === "task_update" || event.type === "task_handoff") {
      const task = event.data as TaskItem;
      const accepted = this.upsert(task, source);
      // A service source can change descendant readiness/validity, and a child
      // can change sibling allowances. Always read server projections, even
      // when the intermediate source or descendant is absent from this mirror.
      if (accepted && (task.collaboration?.dispatch?.source === "api"
        || Object.keys(task.chain_budget ?? {}).length > 0)) this.refreshAfterEvent(source);
      return accepted;
    }
    return false;
  };

  private refreshAfterEvent(client: TaskClient) {
    if (this.eventRefresh?.client === client && this.eventRefresh.epoch === this.epoch) return;
    const pending = { client, epoch: this.epoch, dirty: true };
    this.eventRefresh = pending;
    // Merge update/handoff bursts; retain at most one read and one dirty bit.
    queueMicrotask(async () => {
      try {
        while (this.eventRefresh === pending && this.getClient() === client
          && this.epoch === pending.epoch && pending.dirty) {
          pending.dirty = false;
          await this.refreshTasks();
          // Any newer upsert marks this dirty, including an unrelated event
          // that invalidated the in-flight response's revision guard.
        }
      } finally {
        if (this.eventRefresh === pending) this.eventRefresh = null;
      }
    });
  }

  refreshTasks = async () => {
    const request = this.capture();
    if (!request.client) return;
    const serial = ++this.taskRequest;
    const revision = this.taskRevision;
    try {
      const tasks = await request.client.listTasks();
      if (request.current() && serial === this.taskRequest && revision === this.taskRevision) {
        this.taskRevision += 1;
        this.replace({ ...this.snapshot, tasks });
      }
    } catch {
      // Retain the last authoritative snapshot; WS events can still update it.
    }
  };

  refreshWorktrees = async () => {
    const request = this.capture();
    if (!request.client) return;
    const serial = ++this.worktreeRequest;
    try {
      const worktreeWorkspace = await request.client.getWorktreeWorkspace();
      if (request.current() && serial === this.worktreeRequest) {
        this.replace({ ...this.snapshot, worktreeWorkspace });
      }
    } catch {
      // Older runtimes may not expose the worktree API.
    }
  };
}
