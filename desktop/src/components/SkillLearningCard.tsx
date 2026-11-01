import { useEffect, useId, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { ConnectionState, PersonalSkill, PersonalSkillDraft, SkillLearningSnapshot, SkillLearningSource, SkillProposal } from "../types";
import { PersonalSkillRequests } from "../lib/personalSkills";
import { skillLearningDraft } from "../lib/skillLearning";

const statusText = { pending: "待差异审核", applying: "保存结果待核对", accepted: "已保存版本", rejected: "已拒绝" };
const empty = { snapshot: null as SkillLearningSnapshot | null, sourceId: "", skillId: "", source: null as SkillLearningSource | null,
  draft: null as PersonalSkillDraft | null, proposal: null as SkillProposal | null, busy: false, error: "", notice: "" };

type Props = { client: GatewayClient | null; connection: ConnectionState; skills: PersonalSkill[]; onSaved: (id: string) => Promise<void>; onInspectUncertain?: () => Promise<void> };
export function SkillLearningCard({ client, connection, skills, onSaved, onInspectUncertain }: Props) {
  const requests = useRef(new PersonalSkillRequests()).current;
  const epoch = requests.bind(client, connection === "connected");
  const [view, setView] = useState({ ...empty, epoch });
  const state = view.epoch === epoch ? view : { ...empty, epoch };
  const id = useId();
  function update(patch: Partial<typeof empty>) {
    setView(previous => ({ ...(previous.epoch === requests.epoch ? previous : empty), ...patch, epoch: requests.epoch }));
  }
  async function refresh(current: () => boolean) {
    if (!client || !current()) return;
    const snapshot = await client.getSkillLearning();
    if (current()) update({ snapshot });
  }
  async function operate(action: (current: () => boolean) => Promise<void>) {
    await requests.run(async current => {
      update({ busy: true, error: "" });
      try { await action(current); }
      catch (error) { if (current()) update({ error: `${error instanceof Error ? error.message : "请求未确认"}。若已提交，请刷新提案核对状态。` }); }
      finally { if (current()) update({ busy: false }); }
    });
  }
  useEffect(() => {
    void operate(refresh);
    return () => requests.invalidate();
    // The original Gateway transport epoch also fences same-client reconnects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, connection, client?.connectionEpoch]);

  async function prepare() {
    const source = state.snapshot?.sources.find(value => value.dispatch_id === state.sourceId);
    const skillId = state.skillId.trim();
    if (!source || !/^[a-z][a-z0-9_-]{0,47}$/.test(skillId)) return;
    await operate(async current => {
      if (!client || !current()) return;
      const existing = skills.some(value => value.id === skillId) ? await client.previewPersonalSkill(skillId) : undefined;
      if (current()) update({ skillId, source, draft: skillLearningDraft(source, existing), proposal: null, notice: "请提炼可复用流程，再保存待审草稿；原技能暂不变更。" });
    });
  }
  async function stage() {
    if (!state.draft || !state.source) return;
    const { draft, source, skillId } = state;
    await operate(async current => {
      if (!client || !current()) return;
      const proposal = await client.proposePersonalSkill(skillId, source, draft);
      if (!current()) return;
      update({ proposal, draft: null, notice: "草稿已保存，尚未应用或启用。请核对来源与差异。" });
      await refresh(current);
    });
  }
  async function preview(skillId: string) {
    await operate(async current => {
      if (!client || !current()) return;
      const proposal = await client.previewSkillProposal(skillId);
      if (!current()) return;
      update({ proposal, draft: null, notice: "" });
      if (proposal.status === "applying") await onInspectUncertain?.();
    });
  }
  async function decide(action: "accept" | "reject" | "reconcile") {
    const proposal = state.proposal;
    if (!proposal) return;
    await operate(async current => {
      if (!client || !current()) return;
      const result = await client.decideSkillProposal(proposal, action);
      if (!current()) return;
      update({ proposal: result, notice: action === "reject" ? "已结束提案，当前技能内容保持原状。" : "已记录保存结果；请在上方审阅当前正文，启用须另行确认。" });
      await refresh(current);
      if (current() && action !== "reject") await onSaved(result.skill_id);
    });
  }
  const disabled = connection !== "connected" || state.busy;
  const proposal = state.proposal;
  return <section className="skill-learning" aria-label="任务复用与技能提案">
    <div className="extensions-inspect-heading"><h3>从任务起草技能</h3><button disabled={disabled} onClick={() => void operate(async current => {
      await refresh(current); if (current()) update({ proposal: null, draft: null, notice: "清单已刷新，请重新打开提案核对状态。" });
    })}>刷新提案</button></div>
    <p>只列出记录中验证通过、当前提交全部改动已审阅的交付。不代表已上线或目标已达成；这里不连接项目重新验证，不调用模型。</p>
    {state.error && <p role="alert">{state.error}</p>}{state.notice && <p role="status">{state.notice}</p>}
    {state.snapshot?.issues.map(issue => <p role="alert" key={issue}>{issue}</p>)}
    {state.snapshot?.truncated && <p>清单已截断，仅显示有限来源与提案。</p>}
    {state.snapshot?.sources.length === 0 && <p>暂无符合条件的交付。请先在原任务中完成验证与改动审阅，再刷新。</p>}
    <form className="personal-skill-editor" onSubmit={event => { event.preventDefault(); void prepare(); }}>
      <label>来源任务<select disabled={disabled || !!state.draft} required value={state.sourceId} onChange={event => update({ sourceId: event.target.value })}>
        <option value="">选择已审阅交付</option>{state.snapshot?.sources.map(source => <option key={source.dispatch_id} value={source.dispatch_id}>{source.project} · {source.title}</option>)}
      </select></label>
      <label>目标技能 ID<input required pattern="[a-z][a-z0-9_-]{0,47}" maxLength={48} list={`${id}-skills`} disabled={disabled || !!state.draft}
        value={state.skillId} onChange={event => update({ skillId: event.target.value })} placeholder="例如 release-checklist" /></label>
      <datalist id={`${id}-skills`}>{skills.map(skill => <option key={skill.id} value={skill.id}>{skill.name}</option>)}</datalist>
      <p>填写新 ID 创建技能，或选择已有 ID 提议新版本。草稿不会改变现有启用状态。</p>
      <button disabled={disabled || !state.sourceId || !!state.draft} type="submit">准备草稿</button>
    </form>
    {state.draft && <form className="personal-skill-editor" onSubmit={event => { event.preventDefault(); void stage(); }}>
      <h4>编辑待审草稿 · {state.skillId}</h4><p>根据任务目标和验收清单起草，未提取执行日志；请补充并核对适用范围和操作步骤。</p>
      <label>名称<input required maxLength={80} disabled={disabled} value={state.draft.name} onChange={event => update({ draft: { ...state.draft!, name: event.target.value } })} /></label>
      <label>说明<input maxLength={300} disabled={disabled} value={state.draft.description} onChange={event => update({ draft: { ...state.draft!, description: event.target.value } })} /></label>
      <label>流程指令<textarea required maxLength={60000} rows={10} disabled={disabled} value={state.draft.instructions} onChange={event => update({ draft: { ...state.draft!, instructions: event.target.value } })} /></label>
      <div><button disabled={disabled} type="submit">保存待审草稿</button>{" "}<button disabled={disabled} type="button" onClick={() => update({ draft: null })}>取消起草</button></div>
    </form>}
    <div className="extensions-inspect-list">{state.snapshot?.proposals.map(item => <div className="extensions-inspect-item" key={item.skill_id}>
      <strong>{item.skill_id} · {statusText[item.status]}</strong><small>来源：{item.source.project} / {item.source.title}</small>
      <button disabled={disabled} onClick={() => void preview(item.skill_id)}>查看提案与记录</button>
    </div>)}</div>
    {proposal && <SkillProposalReview proposal={proposal} disabled={disabled} onDecide={action => void decide(action)} />}
    <p>由本实例操作人提出和审阅，目前不区分独立账号。无自动学习、自动启用或后台重试。</p>
  </section>;
}

export function SkillProposalReview({ proposal, disabled, onDecide }: {
  proposal: SkillProposal; disabled: boolean; onDecide: (action: "accept" | "reject" | "reconcile") => void;
}) {
  return <div className="personal-skill-review" aria-label="技能提案差异审核">
    <h4>{proposal.draft.name} · {statusText[proposal.status]}</h4>
    <p>来源：{proposal.source.project} / {proposal.source.title}</p>
    <small>任务 {proposal.source.dispatch_id} · 提交 {proposal.source.head.slice(0, 12)}</small>
    <small>交付观察时间 {proposal.source.checked_at} · 基准版本 {proposal.draft.revision?.slice(0, 12) || "新建"}</small>
    <p>提出时间 {proposal.created_at}；{proposal.reviewed_at ? `审阅时间 ${proposal.reviewed_at}` : "尚未审阅"}。操作人：本实例操作人。</p>
    {proposal.status === "pending" && !proposal.stale && <><p>以下包含名称、说明和正文的全部差异：</p><pre aria-label="技能版本差异">{proposal.diff || "内容相同，保存仍需重新启用。"}</pre></>}
    {proposal.diff_redacted && <p>旧版本的疑似凭据已在差异展示中脱敏，请另行核对。</p>}
    {proposal.status === "pending" && proposal.stale && <p role="alert">技能基准版本已变化，不能应用此提案。请拒绝后重新起草。</p>}
    {proposal.status === "pending" && proposal.source_stale && <p role="alert">来源交付证据已变化或无法读取，不能应用此提案；请回到原任务核对。</p>}
    {proposal.status === "pending" && <><p>确认保存将停用此技能，随后还须审阅正文并单独启用。</p>
      <button disabled={disabled || proposal.stale || proposal.source_stale} onClick={() => onDecide("accept")}>已审阅差异，保存为停用版本</button>{" "}
      <button disabled={disabled} onClick={() => onDecide("reject")}>拒绝提案</button></>}
    {proposal.status === "applying" && <><p role="alert">上次保存结果待核对，不会自动重试。当前版本 {proposal.current_revision?.slice(0, 12) || "不存在"}，预期版本 {proposal.target_revision.slice(0, 12)}。</p>
      <button disabled={disabled || proposal.current_revision !== proposal.target_revision || proposal.current_enabled} onClick={() => onDecide("reconcile")}>核对已写入版本</button>{" "}
      <button disabled={disabled} onClick={() => onDecide("reject")}>结束提案，保留当前技能</button></>}
    {proposal.status === "accepted" && <p>提案已保存为版本，启用状态请在上方当前技能中核对。</p>}
    {proposal.history.length > 0 && <details><summary>较早记录（{proposal.history.length}）</summary>{proposal.history.map(item => <p key={item.id}>{statusText[item.status]} · {item.reviewed_at || "无审阅时间"} · {item.id.slice(0, 12)}</p>)}</details>}
  </div>;
}
