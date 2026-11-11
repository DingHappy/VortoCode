import { useEffect, useId, useState } from "react";
import type { GatewayClient } from "../gateway";
import { decideNotes, type DepartmentDetail } from "../lib/departments";
import { useTaskAction } from "../hooks/useTaskAction";
import { RoleGrants } from "./settings/StaffPane";
import { ResultDocument } from "./ResultDocument";
import "./DepartmentPage.css";

type Props = { id: string; client: GatewayClient | null; getClient: () => GatewayClient | null; connected: boolean;
  onDraft: (text: string) => void; onOpenTask: (id: string) => void; onUpdated: () => void };
export function DepartmentPage({ id, client, getClient, connected, onDraft, onOpenTask, onUpdated }: Props) {
  const [detail, setDetail] = useState<DepartmentDetail | null>(null), [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    if (!client || !connected) { setError("连接已断开，当前记录不能用于新的决定"); return; }
    void client.readDepartment(id).then(value => {
      if (active && getClient() === client) { setDetail(value); setError(""); }
    }).catch(() => { if (active && getClient() === client) setError("部门详情暂不可读取，请刷新后核对"); });
    return () => { active = false; };
  }, [id, client, connected, revision]);
  const refresh = () => { setRevision(value => value + 1); onUpdated(); };
  return <DepartmentView detail={detail} error={error} disabled={!connected || !!error || !client || getClient() !== client}
    refresh={refresh} onDraft={onDraft} onOpenTask={onOpenTask}
    notes={detail ? <NotesForm key={`${id}:${detail.department.revision}:${detail.notes.current.revision}:${detail.notes.pending.map(item => item.digest).join(":")}:${detail.notes.applying?.request ?? ""}`}
      detail={detail} client={client} getClient={getClient} onUpdated={refresh} disabled={!connected || !!error} /> : null} />;
}

export function DepartmentView({ detail, error, disabled, refresh, onDraft, onOpenTask, notes }: {
  detail: DepartmentDetail | null; error: string; disabled: boolean; refresh: () => void;
  onDraft: Props["onDraft"]; onOpenTask: Props["onOpenTask"]; notes?: React.ReactNode;
}) {
  const [prompt, setPrompt] = useState(""), [member, setMember] = useState("");
  const taskId = useId();
  if (!detail) return <section className="department-page"><p role={error ? "alert" : "status"}>{error || "正在读取部门…"}</p><button className="vc-btn vc-btn--secondary" onClick={refresh}>刷新部门</button></section>;
  const d = detail.department;
  const only = detail.members.length === 1 ? detail.members[0] : null;
  const target = detail.members.find(item => item.id === member) ?? only;
  const number = detail.usage.departments?.[d.id];
  return <section className="department-page" aria-label="部门详情">
    <header><div><h2>{only?.name || d.name}</h2><p>{d.duty}</p></div><button className="vc-btn vc-btn--secondary" onClick={refresh}>刷新部门</button></header>
    {error && <p role="alert">{error}。下面保留上次的记录，决定已停用。</p>}
    <div className="department-columns">
      <section aria-label="部门经过" className="department-timeline">
        <h3>交给部门的事</h3>
        <form onSubmit={event => { event.preventDefault(); if (target && prompt.trim()) onDraft(`请把下面这件事交给已登记专员「${target.name}」（${target.id}），沿用岗位权限：\n${prompt.trim()}`); }}>
          {!only && <label>交给谁<select value={member} onChange={event => setMember(event.target.value)} disabled={disabled}>
            <option value="">选择专员</option>{detail.members.map(item => <option key={item.id} value={item.id} disabled={item.status !== "active"}>{item.name}</option>)}
          </select></label>}
          <label htmlFor={taskId}>要做的事</label><textarea id={taskId} maxLength={4000} value={prompt} onChange={event => setPrompt(event.target.value)} disabled={disabled} />
          <button className="vc-btn vc-btn--primary" type="submit" disabled={disabled || !target || target.status !== "active" || !prompt.trim()}>交给助理派活</button>
          <small>先带回主要聊天核对，不会直接派发或附加权限。</small>
        </form>
        {!detail.tasks.length && <p>{detail.tasks_complete ? "还没有部门任务。" : "这批记录未包含部门任务，旧任务尚未全部读取。"}</p>}
        {detail.tasks.length > 0 && !detail.tasks_complete && <p>以下为最近一批记录，旧任务尚未全部读取。</p>}
        {detail.tasks.map(task => <article key={task.id} className="department-task">
          <strong>{task.prompt}</strong><p>{task.collaboration?.assignee} · 第 {task.collaboration?.round ?? 1} 轮 ·
            {task.collaboration?.quota?.status === "waiting" && task.status === "blocked" ? "等待额度" : task.status === "done" ? "已交回，仍需核对" : task.status === "blocked" ? "等主人处理" : task.status === "running" ? "进行中" : task.status === "queued" ? "排队中" : "已停下，请核对"}</p>
          {task.collaboration?.quota?.status === "waiting" && task.status === "blocked" && <p>{task.collaboration.quota.reason}</p>}
          {task.error && <p role="alert">{task.error}</p>}
          {task.result && <ResultDocument title="查看交付原文" text={task.result} tainted={task.collaboration?.tainted} />}
          {task.collaboration?.notes_error && <p role="alert">{task.collaboration.notes_error}</p>}
          <div className="task-actions"><button className="vc-btn vc-btn--secondary" onClick={() => onOpenTask(task.id)}>查看任务</button>
            <button className="vc-btn vc-btn--secondary" disabled={disabled} onClick={() => onDraft(`请查看部门任务 ${task.id} 的当前状态和交付，核对后告诉我结论；不要把已交回当成已验收。`)}>让助理核对</button></div>
        </article>)}
      </section>
      <aside aria-label="部门成员与笔记">
        <section><h3>用量与上限</h3><p>{detail.usage.error || `今天已记录 ${number ?? 0} token · 部门每日上限 ${d.limits.daily_tokens || "不限"}`}</p>
          <p>同时执行最多 {d.limits.parallel} 件。额度在调用之间检查，已在途请求可能超出剩余额度。</p>
          <button className="vc-btn vc-btn--secondary" disabled={disabled} onClick={() => onDraft(`请提议调整部门 ${d.id} 的并发或每日 token 上限，先向我确认新数值，保留成员和资料边界。`)}>调整部门上限</button>
        </section>
        <details><summary>成员与岗位 · {detail.members.length}</summary>{detail.members.map(item => {
          const role = detail.roles.find(role => role.id === item.role);
          return <article key={item.id}><strong>{item.name}</strong>{role ? <RoleGrants role={role} /> : <p>岗位暂不可核对，不能派活。</p>}</article>;
        })}</details>
        {notes ?? <section><h3>部门笔记 · 第 {detail.notes.current.revision} 版</h3><pre>{detail.notes.current.text || "还没有已确认的笔记"}</pre></section>}
      </aside>
    </div>
  </section>;
}

export function NotesForm({ detail, client, getClient, onUpdated, disabled }: { detail: DepartmentDetail; client: GatewayClient | null;
  getClient: Props["getClient"]; onUpdated: () => void; disabled: boolean }) {
  const [selected, setSelected] = useState<string[]>([]), [owner, setOwner] = useState(""), [isPublic, setPublic] = useState(false);
  const { busy, saved, error, run } = useTaskAction(client, getClient, onUpdated);
  const id = useId(), publicDepartment = !detail.roles.some(role => role.grants.sees.length > 0);
  const locked = disabled || busy || saved;
  const decide = (approve: boolean, ownerText = "") => run((client, current) => decideNotes(client, detail, ownerText ? [] : selected, approve, ownerText, isPublic, current));
  return <section className="department-notes" aria-label="部门工作笔记">
    <h3>部门笔记 · 第 {detail.notes.current.revision} 版</h3>
    <details><summary>已确认的全文</summary><pre>{detail.notes.current.text || "还没有笔记"}</pre></details>
    {detail.notes.error && <p role="alert">{detail.notes.error}</p>}
    {detail.notes.applying ? <>
      <p>原决定已批准，保存中断。核对原目标后只完成同一次保存。</p>
      <details><summary>核对原批准内容</summary><pre>{detail.notes.applying.target.text}</pre></details>
      <button className="vc-btn vc-btn--secondary" disabled={locked} onClick={() => void run(async (client, current) => {
        const fresh = await client.readDepartment(detail.department.id);
        if (!current()) return;
        if (fresh.notes.applying?.request !== detail.notes.applying?.request) throw new Error("原保存决定已变化，请刷新");
        await client.decideDepartmentNotes(detail.department.id, { cancel_save: fresh.notes.applying!.request });
      })}>取消这次未完成保存</button>
      <button className="vc-btn vc-btn--primary" disabled={locked} onClick={() => void run(async (client, current) => {
        const fresh = await client.readDepartment(detail.department.id);
        if (!current()) return;
        if (fresh.notes.applying?.request !== detail.notes.applying?.request) throw new Error("原保存决定已变化，请刷新");
        await client.decideDepartmentNotes(detail.department.id, { resume: fresh.notes.applying!.request });
      })}>完成原批准的保存</button>
    </> : <>
      {publicDepartment && <label className="department-public"><input type="checkbox" checked={isPublic} disabled={locked}
        onChange={event => setPublic(event.target.checked)} />按公开资料保存：这些笔记可供联网专员读取</label>}
      {detail.notes.pending.length > 0 && <>
        <p>建议不会自动保存。来自外部内容的条目默认不勾选；核对原文和来源后再选。</p>
        {detail.notes.pending.map(item => <label className="department-suggestion" key={item.id}><input type="checkbox" checked={selected.includes(item.id)} disabled={locked}
          onChange={event => setSelected(previous => event.target.checked ? [...previous, item.id] : previous.filter(id => id !== item.id))} />
          <span>{item.text}<small>{item.member} · {item.task_id} · 来源：{item.sources.join("、") || "公开任务资料"}{item.tainted ? " · 外部建议" : ""}</small></span></label>)}
        <div className="task-actions"><button className="vc-btn vc-btn--secondary" disabled={locked || !selected.length} onClick={() => void decide(false)}>丢弃所选</button>
          <button className="vc-btn vc-btn--primary" disabled={locked || !selected.length || publicDepartment && !isPublic} onClick={() => void decide(true)}>保存所选</button></div>
      </>}
      <label htmlFor={id}>你的偏好或惯例（直接保存）</label><textarea id={id} maxLength={2000} value={owner} disabled={locked} onChange={event => setOwner(event.target.value)} />
      <button className="vc-btn vc-btn--primary" disabled={locked || !owner.trim() || publicDepartment && !isPublic} onClick={() => void decide(true, owner.trim())}>保存我的原话</button>
    </>}
    {error && <p role="alert">{error}</p>}{saved && <p role="status">决定已保存，正在刷新</p>}
  </section>;
}
