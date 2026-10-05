import { useEffect, useId, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { TaskItem } from "../types";
import { taskHandoffIdentity } from "../lib/taskHandoff";
import { useTaskAction } from "../hooks/useTaskAction";

type Props = { task: TaskItem; getClient: () => GatewayClient | null; onUpdated: () => void };

export function TaskHandoffActions({ task, getClient, onUpdated }: Props) {
  const identity = taskHandoffIdentity(task);
  if (!identity) return null;
  return <HandoffForm key={`${task.id}:${identity.session}:${identity.revision}`}
    taskId={task.id} {...identity} client={getClient()} getClient={getClient} onUpdated={onUpdated} />;
}

function HandoffForm({ taskId, session, revision, client, getClient, onUpdated }: {
  taskId: string; session: string; revision: string; client: GatewayClient | null;
  getClient: Props["getClient"]; onUpdated: Props["onUpdated"];
}) {
  const noteId = useId();
  const [note, setNote] = useState("");
  const { busy, saved, error, run } = useTaskAction(client, getClient, onUpdated);
  useEffect(() => { setNote(""); }, [client]);

  async function acknowledge() {
    if (!note.trim()) return;
    await run(async (client) => {
      const receipt = await client.acknowledgeTaskHandoff(taskId, session, revision, note.trim());
      if (!receipt || receipt.task_id !== taskId || receipt.revision !== revision || receipt.handled !== true) {
        throw new Error("未收到匹配的处理回执，请刷新任务后重试");
      }
    });
  }

  return <details className="task-handoff-actions">
    <summary>记录交接处理</summary>
    <p>记录已查看和处理结果。任务验收单独记录。</p>
    <form onSubmit={(event) => { event.preventDefault(); void acknowledge(); }}>
      <label htmlFor={noteId}>处理说明</label>
      <textarea id={noteId} value={note} maxLength={2000} required disabled={busy || saved}
        placeholder="例如：已核对结果，并向用户说明后续安排"
        onChange={(event) => setNote(event.target.value)} />
      {error && <div className="task-error" role="alert">{error}</div>}
      {saved && <p role="status">处理说明已保存</p>}
      {!client && <p role="status">连接运行时后可记录处理结果</p>}
      <div className="task-actions"><button type="submit" disabled={!client || busy || saved || !note.trim()}>
        {busy ? "保存中…" : saved ? "已保存" : "标记已处理"}
      </button>
        {error && client && !busy && <button type="button" onClick={onUpdated}>刷新任务</button>}
      </div>
    </form>
  </details>;
}
