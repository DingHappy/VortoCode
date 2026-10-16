import { useEffect, useMemo, useRef, useState } from "react";
import { goalDate, localGoalDate, personalGoalActions, type PersonalGoalClient } from "../lib/personalGoals";
import type { PersonalGoal } from "../types";

export function AssistantGoals({ client, connected }: { client: PersonalGoalClient | null; connected: boolean }) {
  const identity = useRef({ client, connected });
  identity.current = { client, connected };
  const [snapshot, setSnapshot] = useState<{ client: PersonalGoalClient; goals: PersonalGoal[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [objective, setObjective] = useState("");
  const [criteria, setCriteria] = useState("");
  const [next, setNext] = useState("");
  const [due, setDue] = useState("");
  const [followUp, setFollowUp] = useState("");
  const requestId = useMemo(() => crypto.randomUUID(), [objective, criteria, next, due, followUp]);
  const actionsRef = useRef<ReturnType<typeof personalGoalActions> | null>(null);
  useEffect(() => {
    setBusy(false); setError("");
    if (!client || !connected) return;
    const actions = personalGoalActions(client,
      () => identity.current.client === client && identity.current.connected, {
        goals: goals => setSnapshot({ client, goals }), busy: setBusy, error: setError,
      });
    actionsRef.current = actions;
    void actions.refresh();
    return () => { actions.dispose(); if (actionsRef.current === actions) actionsRef.current = null; };
  }, [client, connected]);
  useEffect(() => { setObjective(""); setCriteria(""); setNext(""); setDue(""); setFollowUp(""); }, [client]);
  const goals = snapshot?.client === client ? snapshot?.goals ?? [] : [];
  const disabled = !connected || !client || busy;
  async function create() {
    const actions = actionsRef.current;
    if (!client || !actions) return;
    const saved = await actions.write(() => client.createPersonalGoal({ request_id: requestId, objective,
      acceptance_criteria: criteria.split("\n").map(s => s.trim()).filter(Boolean), next_action: next,
      due_at: goalDate(due), follow_up_at: goalDate(followUp),
    }), `personal-${requestId}`);
    if (saved) { setObjective(""); setCriteria(""); setNext(""); setDue(""); setFollowUp(""); }
  }
  return <section className="assistant-section assistant-goals">
    <h5>个人目标 <button disabled={disabled} onClick={() => void actionsRef.current?.refresh()}>刷新</button></h5>
    <p>写清下一步和达标条件，逐条核对后再结束。跟进时间目前用于提示，自动提醒尚未接入。</p>
    {!connected && <p role="status">连接后可读取和修改目标。</p>}
    {error && <p role="alert">{error}</p>}
    <details><summary>新建目标</summary><form onSubmit={event => { event.preventDefault(); void create(); }}>
      <fieldset disabled={disabled}>
        <label>想达成什么<input required maxLength={500} value={objective} onChange={event => setObjective(event.target.value)} /></label>
        <label>达标条件（每行一条）<textarea required value={criteria} maxLength={10000} onChange={event => setCriteria(event.target.value)} /></label>
        <label>下一步<input required maxLength={1000} value={next} onChange={event => setNext(event.target.value)} /></label>
        <label>期限<input type="datetime-local" value={due} onChange={event => setDue(event.target.value)} /></label>
        <label>下次跟进<input type="datetime-local" value={followUp} onChange={event => setFollowUp(event.target.value)} /></label>
        <button type="submit">保存目标</button>
      </fieldset>
    </form></details>
    {connected && snapshot?.client === client && !goals.length && <p>还没有个人目标。</p>}
    {goals.map(goal => <PersonalGoalCard key={`${goal.id}:${goal.revision}`} goal={goal} disabled={disabled}
      onSave={async changes => { if (client) await actionsRef.current?.write(() => client.updatePersonalGoal(goal, changes), goal.id); }}
      onConfirm={async (id, summary, passed) => { if (client) await actionsRef.current?.write(() => client.confirmPersonalGoal(goal, id, summary, passed), goal.id); }} />)}
  </section>;
}

export function PersonalGoalCard({ goal, disabled, onSave, onConfirm }: {
  goal: PersonalGoal; disabled: boolean;
  onSave: (changes: Pick<PersonalGoal, "next_action" | "blocker" | "due_at" | "follow_up_at">) => Promise<void>;
  onConfirm: (criterionId: string, summary: string, passed: boolean) => Promise<void>;
}) {
  const [next, setNext] = useState(goal.next_action ?? "");
  const [blocker, setBlocker] = useState(goal.blocker ?? "");
  const [due, setDue] = useState(localGoalDate(goal.due_at));
  const [followUp, setFollowUp] = useState(localGoalDate(goal.follow_up_at));
  const [evidence, setEvidence] = useState<Record<string, string>>({});
  const achieved = goal.status === "achieved";
  return <article className="assistant-goal-card" aria-label={`个人目标：${goal.objective}`}>
    <strong>{goal.objective}</strong>
    <p>{achieved ? "已达成" : goal.status === "blocked" ? "有阻塞" : "推进中"} · {goal.progress.passed}/{goal.progress.total} 条已达标
      {goal.overdue && " · 已过期限"}{goal.follow_up_due && " · 到了跟进时间"}</p>
    <fieldset disabled={disabled || achieved}>
      <label>下一步<input value={next} maxLength={1000} onChange={e => setNext(e.target.value)} /></label>
      <label>阻塞<input value={blocker} maxLength={1000} onChange={e => setBlocker(e.target.value)} /></label>
      <label>期限<input type="datetime-local" value={due} onChange={e => setDue(e.target.value)} /></label>
      <label>下次跟进<input type="datetime-local" value={followUp} onChange={e => setFollowUp(e.target.value)} /></label>
      <button disabled={!next.trim()} onClick={() => void onSave({ next_action: next, blocker, due_at: goalDate(due), follow_up_at: goalDate(followUp) })}>保存进展</button>
    </fieldset>
    {goal.acceptance_criteria.map(criterion => {
      const ids = criterion.evidence_ids ?? [];
      const latest = goal.evidence.find(item => item.id === ids[ids.length - 1]);
      return <details key={criterion.id}><summary>{criterion.text} · {criterion.status === "passed" ? "已达标" : criterion.status === "failed" ? "未通过" : "待核对"}</summary>
        {latest && <p>最近验收：{latest.summary}</p>}
        <label>验收依据<textarea disabled={disabled} maxLength={2000} value={evidence[criterion.id] ?? ""}
          onChange={e => setEvidence({ ...evidence, [criterion.id]: e.target.value })} /></label>
        <button disabled={disabled || !evidence[criterion.id]?.trim()} onClick={() => void onConfirm(criterion.id, evidence[criterion.id], true)}>确认达标</button>
        <button disabled={disabled || !evidence[criterion.id]?.trim()} onClick={() => void onConfirm(criterion.id, evidence[criterion.id], false)}>记录未通过</button>
      </details>;
    })}
  </article>;
}
