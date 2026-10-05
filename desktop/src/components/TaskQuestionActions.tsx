import { useEffect, useId, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { TaskItem } from "../types";
import { taskQuestionIdentity } from "../lib/taskQuestion";
import { useTaskAction } from "../hooks/useTaskAction";

type Props = { task: TaskItem; getClient: () => GatewayClient | null; onUpdated: () => void };
type Identity = NonNullable<ReturnType<typeof taskQuestionIdentity>>;

export function TaskQuestionActions({ task, getClient, onUpdated }: Props) {
  const identity = taskQuestionIdentity(task);
  if (task.status !== "blocked" || task.dependencies?.result_valid === false) return null;
  if (!identity) return <div className="task-question-actions">
    <p>当前问题已过期或无法回答，请刷新任务后检查。</p>
    <button onClick={onUpdated}>刷新任务</button>
  </div>;
  return <AnswerForm key={`${task.id}:${identity.session}:${identity.round}:${identity.question.id}`}
    identity={identity} taskId={task.id} client={getClient()} getClient={getClient} onUpdated={onUpdated} />;
}

function AnswerForm({ identity, taskId, client, getClient, onUpdated }: {
  identity: Identity; taskId: string; client: GatewayClient | null;
  getClient: Props["getClient"]; onUpdated: Props["onUpdated"];
}) {
  const { question, session, round } = identity;
  const id = useId();
  const [answer, setAnswer] = useState("");
  const { busy, saved, error, run } = useTaskAction(client, getClient, onUpdated);
  useEffect(() => { setAnswer(""); }, [client]);

  async function submit() {
    if (!answer.trim()) return;
    await run(async (client) => {
      const result = await client.answerTaskQuestion(taskId, session, round, question.id, answer.trim(), identity.development === true);
      if (!result || result.id !== taskId || result.session !== `sid-${session}` || result.answered_question_id !== question.id
          || !result.questions?.some((q) => q.id === question.id && q.task_id === taskId && q.round === round
            && q.status === "answered" && q.resumed_round === round + 1)) {
        throw new Error("未收到匹配的回答记录，请刷新任务后检查");
      }
    });
  }

  return <section className="task-question-actions" aria-label="回答子 Agent 的问题">
    <strong>需要你补充信息</strong>
    <p>{question.question}</p>
    {identity.development && <small>计划 {question.plan_id} · 当前块 {question.block_id} · 未提交改动已清理</small>}
    {question.context && <details><summary>已完成的调查与缺失信息</summary><pre>{question.context}</pre></details>}
    <small>回答后使用第 {round + 1}/3 轮继续{identity.development ? "开发" : "研究"}。问题有效期至 {new Date(question.expires_at).toLocaleString()}。</small>
    <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      {question.options.length > 0 && <div className="task-actions">
        {question.options.map((option, index) => <button key={index} type="button" disabled={busy || saved}
          onClick={() => setAnswer(option)}>{option}</button>)}
      </div>}
      <label htmlFor={id}>你的回答</label>
      <textarea id={id} value={answer} maxLength={2000} required disabled={busy || saved}
        onChange={(event) => setAnswer(event.target.value)} placeholder="填写所需信息，也可以修改建议答案" />
      {error && <div className="task-error" role="alert">{error}</div>}
      {saved && <p role="status">回答已保存</p>}
      <div className="task-actions">
        <button type="submit" disabled={!client || busy || saved || !answer.trim()}>{busy ? "提交中…" : saved ? "已提交" : "回答并继续"}</button>
        {error && !busy && <button type="button" onClick={onUpdated}>刷新任务</button>}
      </div>
    </form>
  </section>;
}
