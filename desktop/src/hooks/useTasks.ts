import { useCallback, useRef, useSyncExternalStore } from "react";
import type { RefObject } from "react";
import type { GatewayClient } from "../gateway";
import type { TaskItem } from "../types";
import { errorText } from "../lib/errorText";
import { TaskFeed } from "../tasks/taskFeed";

/** App owns connections and cross-domain UI; this hook owns the task mirror. */
export function useTasks(clientRef: RefObject<GatewayClient | null>, onBanner: (text: string) => void) {
  const latest = useRef({ clientRef, onBanner });
  latest.current = { clientRef, onBanner };
  const feedRef = useRef<TaskFeed | null>(null);
  if (!feedRef.current) feedRef.current = new TaskFeed(() => latest.current.clientRef.current);
  const feed = feedRef.current;
  const snapshot = useSyncExternalStore(feed.subscribe, feed.getSnapshot);

  const cancelTask = useCallback(async (id: string) => {
    const request = feed.capture();
    if (!request.client) return;
    try {
      await request.client.cancelTask(id);
    } catch (error) {
      if (request.current()) latest.current.onBanner(errorText(error, "取消失败"));
    }
    if (!request.current()) return;
    await feed.refreshTasks();
    if (request.current()) await feed.refreshWorktrees();
  }, [feed]);

  const pauseTask = useCallback(async (id: string) => {
    const request = feed.capture();
    if (!request.client) return;
    try {
      const paused = await request.client.pauseTask(id);
      if (!request.current()) return;
      feed.upsert(paused, request.client);
      latest.current.onBanner("任务已暂停；当前一次性 worktree 已清理，持久计划可随时恢复");
      await feed.refreshWorktrees();
    } catch (error) {
      if (request.current()) latest.current.onBanner(errorText(error, "暂停任务失败"));
    }
  }, [feed]);

  const resumeTask = useCallback(async (task: TaskItem) => {
    const request = feed.capture();
    if (!request.client) return;
    try {
      const resumed = await request.client.resumeTask(task.id);
      if (!request.current()) return;
      feed.upsert(resumed, request.client);
      feed.focus(resumed.id);
      latest.current.onBanner(resumed.replayed
        ? "已找到原恢复任务；重复请求不会再次执行，请查看其当前状态"
        : `已从 ${task.plan_id} 恢复；已落地的计划块不会重做`);
      await feed.refreshWorktrees();
    } catch (error) {
      if (request.current()) latest.current.onBanner(errorText(error, "恢复任务失败"));
    }
  }, [feed]);

  return { ...snapshot, refreshTasks: feed.refreshTasks, refreshWorktrees: feed.refreshWorktrees,
    setFocusedTaskId: feed.focus, resetTasks: feed.reset, upsertTask: feed.upsert,
    acceptTaskEvent: feed.acceptEvent, captureTaskScope: feed.capture, cancelTask, pauseTask, resumeTask };
}
