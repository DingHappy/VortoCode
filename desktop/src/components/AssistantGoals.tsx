import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { goalDate, goalFollowUpLabel, localGoalDate, personalGoalActions, type PersonalGoalClient, type PersonalGoalOperation } from "../lib/personalGoals";
import type { PersonalGoal } from "../types";
import type { Ref } from "react";
import { hasConfirmedPersonalCriterion, hasConfirmedPersonalGoal } from "../lib/personalGoalSnapshot";
import { emptyPersonalGoalDraft, personalGoalDraft } from "../lib/personalGoalDraft";
import { freshAt } from "../lib/readFreshness";
import { startWorkdayClock } from "../lib/assistantWorkdayReads";

export function AssistantGoals({ client, connected, focusId, onClearFocus, onFocusGoal }: {
  client: PersonalGoalClient | null; connected: boolean; focusId?: string; onClearFocus?: () => void;
  onFocusGoal?: (id: string, owner: PersonalGoalClient) => void;
}) {
  const source = useMemo(() => ({ client, connected }), [client, connected]);
  const identity = useRef(source);
  identity.current = source;
  const [snapshot, setSnapshot] = useState<{ source: typeof source; goals: PersonalGoal[]; checkedAt: number } | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [operationSnapshot, setOperationSnapshot] = useState<{ source: typeof source; operation: PersonalGoalOperation } | null>(null);
  const operation: PersonalGoalOperation = operationSnapshot?.source === source ? operationSnapshot.operation : { phase: "idle" };
  const focusedCard = useRef<HTMLElement | null>(null);
  const lastFocus = useRef(focusId);
  const [draft, dispatchDraft] = useReducer(personalGoalDraft, undefined, () => emptyPersonalGoalDraft(crypto.randomUUID()));
  const { requestId, objective, criteria, next, due, followUp } = draft;
  function editDraft(field: "objective" | "criteria" | "next" | "due" | "followUp", value: string) {
    dispatchDraft({ type: "edit", field, value, requestId: crypto.randomUUID() });
  }
  const actionsRef = useRef<ReturnType<typeof personalGoalActions> | null>(null);
  useEffect(() => startWorkdayClock(setNow, document), []);
  useEffect(() => {
    setBusy(false); setError(""); setOperationSnapshot(null); setReading(false);
    if (!client || !connected) return;
    const actions = personalGoalActions(client,
      () => identity.current === source, {
        goals: goals => {
          const checkedAt = Date.now();
          setSnapshot({ source, goals, checkedAt });
          setNow(new Date(checkedAt));
          dispatchDraft({ type: "read", goalIds: goals.map(goal => goal.id), requestId: crypto.randomUUID() });
        }, busy: setBusy, error: setError,
        operation: operation => setOperationSnapshot({ source, operation }), reading: setReading,
      });
    actionsRef.current = actions;
    void actions.refresh();
    return () => { actions.dispose(); if (actionsRef.current === actions) actionsRef.current = null; };
  }, [source, client, connected]);
  useEffect(() => {
    if (lastFocus.current !== focusId) {
      lastFocus.current = focusId;
      if (focusId && client && connected) void actionsRef.current?.refresh();
    }
  }, [focusId, client, connected]);
  useEffect(() => { dispatchDraft({ type: "reset", requestId: crypto.randomUUID() }); }, [client]);
  const hasSnapshot = snapshot?.source === source;
  const fresh = hasSnapshot && freshAt(snapshot.checkedAt, now.getTime());
  const goals = snapshot?.source.client === client ? snapshot?.goals ?? [] : [];
  const visibleGoals = focusId ? goals.filter(goal => goal.id === focusId) : goals;
  const checking = connected && Boolean(client) && (reading || (!hasSnapshot && !error));
  useEffect(() => {
    if (focusId && hasSnapshot && !checking && !error && snapshot.goals.some(goal => goal.id === focusId)) {
      focusedCard.current?.scrollIntoView({ block: "nearest" });
      focusedCard.current?.focus({ preventScroll: true });
    }
  }, [focusId, snapshot, hasSnapshot, checking, error]);
  const readDisabled = !connected || !client || busy || checking;
  const verification = operation.phase === "accepted" || (operation.phase === "unconfirmed" && !operation.checked);
  const disabled = readDisabled || verification || operation.phase === "submitting" || !fresh || Boolean(error);
  function writeGoal(run: () => Promise<PersonalGoal>, id: string) {
    // Check again at click time: the read may expire between clock ticks.
    if (disabled || !snapshot || identity.current !== source || !freshAt(snapshot.checkedAt, Date.now())) {
      setNow(new Date());
      return;
    }
    return actionsRef.current?.write(run, id);
  }
  async function create() {
    const actions = actionsRef.current;
    if (disabled || !client || !actions) return;
    await writeGoal(() => {
      dispatchDraft({ type: "submit", requestId });
      return client.createPersonalGoal({ request_id: requestId, objective,
        acceptance_criteria: criteria.split("\n").map(s => s.trim()).filter(Boolean), next_action: next,
        due_at: goalDate(due), follow_up_at: goalDate(followUp),
      });
    }, `personal-${requestId}`);
  }
  return <section className="assistant-section assistant-goals">
    <h5>个人目标 <button disabled={readDisabled} onClick={() => void actionsRef.current?.refresh()}>{operation.phase === "submitting" ? "等待提交结果…" : checking ? "正在核对…"
      : operation.phase === "accepted" ? "刷新核对已提交操作" : operation.phase === "unconfirmed" ? "刷新核对上次操作" : "刷新"}</button></h5>
    <p>写清下一步和达标条件，逐条核对后再结束。到点提醒需要单独批准，由已配置通知的常驻助理发送。</p>
    {!connected && <p role="status">连接后可读取和修改目标。</p>}
    {checking && <p role="status">正在核对目标，暂不能修改。</p>}
    {connected && hasSnapshot && !fresh && <p role="status">目标状态需要重新核对，请刷新后再修改或验收；当前输入会保留。</p>}
    {error && <p role="alert">{error}</p>}
    <GoalOperationNotice operation={operation}
      goal={operation.phase === "idle" ? undefined : goals.find(goal => goal.id === operation.goalId)}
      disabled={!connected || !client} onInspect={onFocusGoal ? () => {
        if (client && connected && identity.current === source && operation.phase !== "idle") onFocusGoal(operation.goalId, client);
      } : undefined} />
    {draft.foundGoalId && <p role="status">已在列表找到这次新建的目标，原输入已清空。</p>}
    {focusId && <div className="goal-focus-notice" role="status">
      <strong>{hasSnapshot && !checking && visibleGoals.length === 1 && !error ? "已定位指定目标" : "定位个人目标"}</strong>
      <p>{!connected ? "连接后核对指定目标。" : error ? "指定目标尚未核对，请刷新后再处理。"
        : checking || !hasSnapshot ? "正在核对指定目标…"
        : visibleGoals.length !== 1 ? "当前来源未找到指定目标，请刷新核对或查看全部目标。"
        : "请核对这条目标的下一步和验收依据。"}</p>
      {onClearFocus && <button onClick={onClearFocus}>查看全部目标</button>}
    </div>}
    <details><summary>新建目标</summary><form onSubmit={event => { event.preventDefault(); void create(); }}>
      <fieldset disabled={disabled}>
        <label>想达成什么<input required maxLength={500} value={objective} onChange={event => editDraft("objective", event.target.value)} /></label>
        <label>达标条件（每行一条）<textarea required value={criteria} maxLength={10000} onChange={event => editDraft("criteria", event.target.value)} /></label>
        <label>下一步<input required maxLength={1000} value={next} onChange={event => editDraft("next", event.target.value)} /></label>
        <label>期限<input type="datetime-local" value={due} onChange={event => editDraft("due", event.target.value)} /></label>
        <label>下次跟进<input type="datetime-local" value={followUp} onChange={event => editDraft("followUp", event.target.value)} /></label>
        <button type="submit">保存目标</button>
      </fieldset>
    </form></details>
    {!focusId && connected && hasSnapshot && !checking && !error && !goals.length && <p>还没有个人目标。</p>}
    {visibleGoals.map(goal => <PersonalGoalCard key={`${goal.id}:${goal.revision}`} goal={goal} disabled={disabled}
      cardRef={focusId === goal.id ? focusedCard : undefined} focused={focusId === goal.id}
      onSave={async changes => { if (client) await writeGoal(() => client.updatePersonalGoal(goal, changes), goal.id); }}
      onConfirm={async (id, summary, passed) => { if (client) await writeGoal(() => client.confirmPersonalGoal(goal, id, summary, passed), goal.id); }}
      onFollowUp={async (enabled, uncertain) => { if (client) await writeGoal(() => client.configurePersonalGoalFollowUp(goal, enabled, uncertain), goal.id); }}
      onAcknowledge={async () => { if (client) await writeGoal(() => client.acknowledgePersonalGoalFollowUp(goal), goal.id); }} />)}
  </section>;
}

export function GoalOperationNotice({ operation, goal, onInspect, disabled = false }: {
  operation: PersonalGoalOperation; goal?: PersonalGoal; onInspect?: () => void; disabled?: boolean;
}) {
  if (operation.phase === "idle") return null;
  const target = goal?.id === operation.goalId ? goal : undefined;
  return <div role="status"><p>{operation.phase === "submitting"
    ? "目标操作正在等待提交结果。离开页面不代表提交已取消，请勿重复提交。"
    : operation.phase === "accepted" ? "上次操作已提交；读取最新目标并核对版本后，才能继续修改。"
    : operation.checked ? "已读取当前目标，但上次提交仍未取得成功回执；请核对内容后再决定是否重试。"
    : "上次提交未取得成功回执；请先刷新当前目标并核对，不要直接重复提交。"}</p>
    {(goal || onInspect) && <p>上次操作目标：<strong>{target?.objective ?? "尚未读取到对应记录"}</strong></p>}
    {onInspect && <button disabled={disabled} aria-label={`核对上次操作目标：${target?.objective ?? "指定目标"}`} onClick={onInspect}>核对这条目标</button>}
  </div>;
}

export function PersonalGoalCard({ goal, disabled, onSave, onConfirm, onFollowUp, onAcknowledge, cardRef, focused }: {
  goal: PersonalGoal; disabled: boolean;
  onSave: (changes: Pick<PersonalGoal, "next_action" | "blocker" | "due_at" | "follow_up_at">) => Promise<void>;
  onConfirm: (criterionId: string, summary: string, passed: boolean) => Promise<void>;
  onFollowUp?: (enabled: boolean, acknowledgeUncertain: boolean) => Promise<void>;
  onAcknowledge?: () => Promise<void>;
  cardRef?: Ref<HTMLElement>; focused?: boolean;
}) {
  const [next, setNext] = useState(goal.next_action ?? "");
  const [blocker, setBlocker] = useState(goal.blocker ?? "");
  const [due, setDue] = useState(localGoalDate(goal.due_at));
  const [followUp, setFollowUp] = useState(localGoalDate(goal.follow_up_at));
  const [evidence, setEvidence] = useState<Record<string, string>>({});
  const achieved = goal.status === "achieved";
  const unknown = !["draft", "active", "blocked", "achieved", "failed"].includes(goal.status)
    || goal.acceptance_criteria.some(item => !["pending", "passed", "failed"].includes(item.status));
  const readOnly = disabled || unknown;
  const status = unknown ? "状态待核对" : achieved ? !disabled && hasConfirmedPersonalGoal(goal) ? "已达成" : "达成记录待核对"
    : goal.status === "blocked" ? "有阻塞" : goal.status === "failed" ? "未达成" : goal.status === "draft" ? "待明确" : "推进中";
  const confirmed = goal.acceptance_criteria.filter(criterion => hasConfirmedPersonalCriterion(goal, criterion)).length;
  const dirty = next !== (goal.next_action ?? "") || blocker !== (goal.blocker ?? "")
    || due !== localGoalDate(goal.due_at) || followUp !== localGoalDate(goal.follow_up_at);
  return <article ref={cardRef} tabIndex={focused ? -1 : undefined} data-personal-goal-id={goal.id}
    className="assistant-goal-card" aria-label={`个人目标：${goal.objective}`}>
    <strong>{goal.objective}</strong>
    <p>{status} · {confirmed}/{goal.progress.total} {disabled ? "条通过记录待核对" : "条已核实达标"}
      {goal.overdue && " · 已过期限"}{goal.follow_up_due && " · 到了跟进时间"}</p>
    {unknown && <p role="status">目标状态尚不能确认，请刷新核对后再操作。</p>}
    <fieldset disabled={readOnly || achieved}>
      <label>下一步<input value={next} maxLength={1000} onChange={e => setNext(e.target.value)} /></label>
      <label>阻塞<input value={blocker} maxLength={1000} onChange={e => setBlocker(e.target.value)} /></label>
      <label>期限<input type="datetime-local" value={due} onChange={e => setDue(e.target.value)} /></label>
      <label>下次跟进<input type="datetime-local" value={followUp} onChange={e => setFollowUp(e.target.value)} /></label>
      <button disabled={!next.trim()} onClick={() => void onSave({ next_action: next, blocker, due_at: goalDate(due), follow_up_at: goalDate(followUp) })}>保存进展</button>
    </fieldset>
    <GoalFollowUp goal={goal} disabled={readOnly || !onFollowUp} dirty={dirty} onChange={onFollowUp} onAcknowledge={onAcknowledge} />
    {goal.acceptance_criteria.map(criterion => {
      const ids = criterion.evidence_ids ?? [];
      const latest = goal.evidence.find(item => item.id === ids[ids.length - 1] && item.criterion_id === criterion.id);
      return <details key={criterion.id}><summary>{criterion.text} · {criterion.status === "passed"
        ? !disabled && hasConfirmedPersonalCriterion(goal, criterion) ? "已达标" : "通过记录待核对" : criterion.status === "failed" ? "未通过" : "待核对"}</summary>
        {latest && <p>最近记录：{latest.summary}</p>}
        <label>验收依据<textarea disabled={readOnly} maxLength={2000} value={evidence[criterion.id] ?? ""}
          onChange={e => setEvidence({ ...evidence, [criterion.id]: e.target.value })} /></label>
        <button disabled={readOnly || !evidence[criterion.id]?.trim()} onClick={() => void onConfirm(criterion.id, evidence[criterion.id], true)}>确认达标</button>
        <button disabled={readOnly || !evidence[criterion.id]?.trim()} onClick={() => void onConfirm(criterion.id, evidence[criterion.id], false)}>记录未通过</button>
      </details>;
    })}
  </article>;
}

export function GoalFollowUp({ goal, disabled, dirty, onChange, onAcknowledge }: {
  goal: PersonalGoal; disabled: boolean; dirty: boolean;
  onChange?: (enabled: boolean, acknowledgeUncertain: boolean) => Promise<void>;
  onAcknowledge?: () => Promise<void>;
}) {
  const [acknowledge, setAcknowledge] = useState(false);
  const state = goal.follow_up;
  const uncertain = state?.status === "unknown" || state?.status === "sending";
  const scheduled = state?.status === "scheduled";
  const known = state?.status === undefined || ["scheduled", "sending", "accepted", "acknowledged", "failed", "unknown", "cancelled"].includes(state.status);
  const canApprove = !disabled && known && !dirty && goal.status !== "achieved" && !!goal.follow_up_at
    && !!goal.follow_up_preview && !!goal.follow_up_runtime_configured && (!uncertain || acknowledge);
  return <section className="assistant-goal-followup" aria-label="到点提醒">
    <strong>一次提醒</strong><p role="status">{goalFollowUpLabel(goal)}</p>
    {state?.reason && <p>{state.reason}</p>}
    {state?.accepted_at && <p>受理记录：{new Date(state.accepted_at).toLocaleString()}</p>}
    {state?.user_acknowledged_at && <p>用户确认：{new Date(state.user_acknowledged_at).toLocaleString()}</p>}
    {!goal.follow_up_runtime_configured && <p>当前运行端未配置助理通知，暂不能批准提醒。</p>}
    {dirty && <p>请先保存进展，再核对并批准提醒正文。</p>}
    {goal.follow_up_preview && <details><summary>将发送给已配对通知渠道的正文</summary><pre>{goal.follow_up_preview}</pre></details>}
    {state?.text && <details><summary>本次已批准的正文</summary><pre>{state.text}</pre></details>}
    {uncertain && <label><input type="checkbox" disabled={disabled} checked={acknowledge} onChange={event => setAcknowledge(event.target.checked)} />
      我已核对此前通知，接受重新批准可能产生重复提醒。</label>}
    {scheduled
      ? <button disabled={disabled || dirty} onClick={() => void onChange?.(false, false)}>暂停提醒</button>
      : <button disabled={!canApprove} onClick={() => void onChange?.(true, uncertain)}>
          {uncertain ? "核对后重新批准" : state?.status === "accepted" || state?.status === "acknowledged" ? "批准再提醒一次" : "批准按时提醒"}
        </button>}
    {!!state?.attempts && state.status !== "acknowledged" && <button disabled={disabled || !known || dirty || !onAcknowledge}
      onClick={() => void onAcknowledge?.()}>我已看到这条提醒</button>}
    <p>到点后随巡检触发；时间已过则下轮处理。修改进展会取消未发送的批准。刷新查看通知结果。</p>
    {!!goal.follow_up_history?.length && <details><summary>以前的提醒记录（最近 20 条）</summary>
      {goal.follow_up_history.map((item, index) => <p key={item.id ?? index}>{item.due_at} · {goalFollowUpLabel({ ...goal, follow_up: item })}</p>)}
    </details>}
  </section>;
}
