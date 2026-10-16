import { useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { ConnectionState, PersonalSkill, PersonalSkillsSnapshot } from "../types";
import { PersonalSkillRequests } from "../lib/personalSkills";

const blank = { id: "", name: "", description: "", instructions: "", revision: null as string | null };

export function PersonalSkillsCard({ client, connection }: { client: GatewayClient | null; connection: ConnectionState }) {
  const requests = useRef(new PersonalSkillRequests()).current;
  const epoch = requests.bind(client, connection === "connected");
  const [view, setView] = useState<{ epoch: number; snapshot: PersonalSkillsSnapshot | null; preview: PersonalSkill | null; draft: typeof blank; editing: boolean; error: string; notice: string; busy: boolean }>(
    { epoch, snapshot: null, preview: null, draft: blank, editing: false, error: "", notice: "", busy: false });
  const state = view.epoch === epoch ? view : { epoch, snapshot: null, preview: null, draft: blank, editing: false, error: "", notice: "", busy: false };
  function update(patch: Partial<typeof view>) {
    const activeEpoch = requests.epoch;
    setView(previous => ({ ...(previous.epoch === activeEpoch ? previous : { ...state, snapshot: null, preview: null, draft: blank }), ...patch, epoch: activeEpoch }));
  }

  async function operate(action: (current: () => boolean) => Promise<void>) {
    await requests.run(async current => {
      update({ busy: true, error: "" });
      try { await action(current); }
      catch (error) { if (current()) update({ error: error instanceof Error ? error.message : "个人技能状态未确认，请刷新后重试。" }); }
      finally { if (current()) update({ busy: false }); }
    });
  }
  async function refresh(current: () => boolean) {
    if (!client || !current()) return;
    const snapshot = await client.getPersonalSkills();
    if (current()) update({ snapshot });
  }
  useEffect(() => {
    void operate(refresh);
    return () => requests.invalidate();
    // Each runtime/connection generation owns its data and in-flight operations.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, connection]);

  async function preview(item: PersonalSkill) {
    await operate(async current => {
      if (!client || !current()) return;
      const result = await client.previewPersonalSkill(item.id);
      if (current()) update({ preview: result, editing: false, notice: "" });
    });
  }
  async function toggle(item: PersonalSkill, enabled: boolean) {
    await operate(async current => {
      if (!client || !current()) return;
      const result = await client.setPersonalSkillEnabled(item.id, enabled, item.revision);
      if (!current()) return;
      update({ preview: null, notice: result.enabled ? "此版本已启用，助理下次加载时生效；尚未执行任务。" : "已禁用后续加载；已进入对话的指令和已发出的操作不会因此撤回。" });
      await refresh(current);
    });
  }
  async function save() {
    const draft = state.draft;
    await operate(async current => {
      if (!client || !current()) return;
      const result = await client.savePersonalSkill(draft.id, { name: draft.name, description: draft.description, instructions: draft.instructions, revision: draft.revision });
      if (!current()) return;
      update({ editing: false, preview: null, draft: blank, notice: "已保存为停用状态，请打开审阅并启用此版本。" });
      await refresh(current);
      if (!current()) return;
      const verified = await client.previewPersonalSkill(result.id);
      if (current()) update({ preview: verified });
    });
  }
  const disabled = connection !== "connected" || state.busy;
  return <section className="extensions-inspect-card personal-skills-card" aria-label="个人技能">
    <div className="extensions-inspect-heading"><strong>个人技能</strong><button disabled={disabled} onClick={() => void operate(refresh)}>刷新技能</button></div>
    <p>独立于项目，仅当前助理实例使用。保存后须审阅并启用；技能只提供指令，不授予执行、文件或外发权限。</p>
    {connection !== "connected" && <p>连接助理后管理个人技能。</p>}
    {state.error && <p role="alert">{state.error}</p>}
    {state.notice && <p role="status">{state.notice}</p>}
    {state.snapshot?.issues.map(issue => <p role="alert" key={issue}>{issue}</p>)}
    {state.snapshot?.truncated && <p role="alert">技能清单已截断，请核对存储目录。</p>}
    {state.snapshot && state.snapshot.items.length === 0 && <p>还没有个人技能，可以添加一套可复用的处理流程。</p>}
    <div className="extensions-inspect-list">{state.snapshot?.items.map(item => <div className="extensions-inspect-item" key={item.id}>
      <strong>{item.name}</strong><small>{item.enabled ? "已启用此版本" : "未启用 / 待审阅"} · {item.id}</small>
      {item.description && <p>{item.description}</p>}
      {!item.model_invocable && <p>此文件禁止模型调用，启用后仍不能由助理加载。</p>}
      <button disabled={disabled} onClick={() => void preview(item)}>审阅正文</button>{" "}
      {item.enabled && <button disabled={disabled} onClick={() => void toggle(item, false)}>禁用后续加载</button>}
    </div>)}</div>
    <button disabled={disabled} onClick={() => update({ editing: true, draft: blank, preview: null, notice: "" })}>添加个人技能</button>
    {state.preview && <div className="personal-skill-review">
      <h3>审阅：{state.preview.name}</h3><small>版本 {state.preview.revision.slice(0, 12)}</small>
      <p>{state.preview.detail} 能力声明仅供参考：{state.preview.capabilities.join(" / ") || "未声明"}。</p>
      <pre>{state.preview.instructions}</pre>
      <button disabled={disabled || !state.preview.model_invocable || state.preview.enabled} onClick={() => void toggle(state.preview!, true)}>已审阅，启用此版本</button>{" "}
      <button disabled={disabled} onClick={() => update({ editing: true, draft: { id: state.preview!.id, name: state.preview!.name, description: state.preview!.description, instructions: state.preview!.instructions || "", revision: state.preview!.revision }, preview: null })}>编辑新版本</button>
    </div>}
    {state.editing && <form className="personal-skill-editor" onSubmit={event => { event.preventDefault(); void save(); }}>
      <label>技能 ID<input required pattern="[a-z][a-z0-9_-]{0,47}" maxLength={48} readOnly={state.draft.revision !== null} disabled={disabled} value={state.draft.id} onChange={event => update({ draft: { ...state.draft, id: event.target.value } })} /></label>
      <label>名称<input required maxLength={80} disabled={disabled} value={state.draft.name} onChange={event => update({ draft: { ...state.draft, name: event.target.value } })} /></label>
      <label>说明<input maxLength={300} disabled={disabled} value={state.draft.description} onChange={event => update({ draft: { ...state.draft, description: event.target.value } })} /></label>
      <label>流程指令<textarea required maxLength={60000} rows={10} disabled={disabled} value={state.draft.instructions} onChange={event => update({ draft: { ...state.draft, instructions: event.target.value } })} /></label>
      <button type="submit" disabled={disabled}>保存为停用版本</button>{" "}<button type="button" disabled={disabled} onClick={() => update({ editing: false })}>取消</button>
    </form>}
    <p>加载记录见原对话的工具时间线与审计；“指令已加载”不代表任务完成。禁用不撤回旧对话内容或远端操作。</p>
  </section>;
}
