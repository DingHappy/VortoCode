import type { GatewayClient } from "../gateway";
import type { TaskItem } from "../types";
import { dispatchTaskIdentity } from "../lib/taskDispatch";
import { useTaskAction } from "../hooks/useTaskAction";

type Props = { task: TaskItem; getClient: () => GatewayClient | null; onUpdated: () => void };

export function TaskDependencyActions(props: Props) {
  const state = props.task.dependencies;
  if (!state?.requires?.length && !state?.failure) return null;
  return <section className="task-dependency-actions">
    <strong>前置任务 · {state.requires?.length ?? 0}</strong>
    {state.requires?.map((item) => <code key={item.task_id} title={item.task_id}>{item.task_id} · 第 {item.round} 轮</code>)}
    {state.failure && <p className="task-error">{state.failure}</p>}
    {state.resolution === "failed" && <p>前置条件未通过。核对后请重新下派任务，选择当前前置轮次。</p>}
    {state.result_valid === false && <p>{state.stop_pending ? "已记录依赖失效，停止尚未确认；请刷新或重新核对。" :
      "此结果的前置条件已失效，不能继续验收或复用。核对后选择有效前置重新下派。"}</p>}
    {state.can_reconcile && <ReconcileForm key={`${props.task.id}:${props.task.owner_session}:${props.task.collaboration?.round}:${state.invalidated}`}
      {...props} />}
    {props.task.status === "waiting" && <ReleaseForm key={`${props.task.id}:${props.task.owner_session}:${props.task.collaboration?.round}`}
      {...props} />}
    {state.resolution === "consumed" && <small>已固定前置结果版本；结果消费与验收、交接回执分别记录。</small>}
  </section>;
}

function ReleaseForm({ task, getClient, onUpdated }: Props) {
  const client = getClient();
  const { busy, saved, error, run } = useTaskAction(client, getClient, onUpdated);
  const identity = dispatchTaskIdentity(task);
  const state = task.dependencies;
  const valid = identity && state?.version === 1 && state.task_id === task.id && state.owner_session === task.owner_session;
  async function release() {
    if (!valid) return;
    await run(async (client) => {
      const result = await client.releaseTaskDependencies(task.id, identity.session, identity.round);
      if (result.id !== task.id || result.session !== task.owner_session || result.dependencies?.released_round !== identity.round
          || !["consumed", "failed", "invalidated"].includes(result.dependencies?.resolution || "")) {
        throw new Error("未收到匹配的依赖处理记录，请刷新任务核对");
      }
    });
  }
  return <>
    <p>{state?.ready ? "前置结果已验收。核对具体结果后可推进。" : "等待前置任务完成并验收；等待不占执行槽。"}</p>
    <div className="task-actions">
      <button disabled={!client || !valid || busy || saved} onClick={() => void release()}>{busy ? "处理中…" : "核对依赖并继续"}</button>
      <button disabled={busy} onClick={onUpdated}>刷新任务</button>
    </div>
    {error && <div className="task-error" role="alert">{error}</div>}
  </>;
}

function ReconcileForm({ task, getClient, onUpdated }: Props) {
  const client = getClient();
  const { busy, saved, error, run } = useTaskAction(client, getClient, onUpdated);
  const identity = dispatchTaskIdentity(task);
  const state = task.dependencies;
  const valid = identity && state?.version === 1 && state.task_id === task.id && state.owner_session === task.owner_session;
  async function reconcile() {
    if (!valid) return;
    await run(async (client) => {
      const result = await client.reconcileTaskDependencies(task.id, identity.session, identity.round);
      if (result.id !== task.id || result.session !== task.owner_session || result.round !== identity.round
          || !["consumed", "invalidated"].includes(result.dependencies?.resolution || "")) {
        throw new Error("未收到匹配的依赖核对记录，请刷新任务");
      }
    });
  }
  return <>
    <div className="task-actions">
      <button disabled={!client || !valid || busy || saved} onClick={() => void reconcile()}>
        {busy ? "核对中…" : ["queued", "running", "blocked"].includes(task.status) ? "核对失效并停止" : "核对并记录依赖失效"}
      </button>
      <button disabled={busy} onClick={onUpdated}>刷新任务</button>
    </div>
    {saved && <small>核对已保存，请以最新任务状态确认执行是否停止。</small>}
    {error && <div className="task-error" role="alert">{error}</div>}
  </>;
}
