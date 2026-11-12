import { useEffect, useMemo, useRef, useState } from "react";
import { confirmAction } from "../../lib/confirm";
import { GatewayClient } from "../../gateway";
import { staffController, type StaffClient, type StaffState, type StaffRole, type StaffMember, type StaffProposal, type StaffBudgetSettings } from "../../lib/staff";
import { StaffBudgets } from "./StaffBudgets";
import { RoutinesPane } from './RoutinesPane';
import { DevelopmentStaff } from './DevelopmentStaff';
import { StaffRecovery } from './StaffRecovery';
import type { RecoveryClient } from '../../lib/staffRecovery';
import type { DevelopmentProposal } from '../../lib/staff';
import type { RoutinesClient } from '../../lib/routines';
import "./StaffPane.css";

const statuses = { active: "可接活", paused: "已暂停", retired: "已撤掉" };
const taskStatus: Record<string, string> = { queued: "待开始", running: "在忙", blocked: "等回答", done: "已交回", failed: "失败", interrupted: "中断", cancelled: "已取消" };
const reviews: Record<string, string> = { pending: "待核对", rework_requested: "待返工", not_submitted: "尚未交付" };
const operations: Record<string, string> = { read: "读取", write: "写文件", execute: "执行", own_data: "主人自己的数据", draft: "草稿" };
export function RoleGrants({ role }: { role: StaffRole }) {
  return <dl className="staff-grants">
    <dt>动作</dt><dd>{role.grants.operations.map(value => operations[value] ?? value).join("、")}</dd>
    <dt>工具</dt><dd>{role.grants.tools.join("、") || "无工具，只阅读交给它的材料"}</dd>
    <dt>私人来源</dt><dd>{role.grants.sees.join("、") || "无"}</dd>
    <dt>联网</dt><dd>{role.grants.web ? "允许查公开资料" : "不联网"}</dd>
    <dt>项目副本</dt><dd>{role.project_binding ? `固定项目：${role.project} · ${role.project_binding.repo_root}；只改一次性副本，交回本地分支；换项目须重新批准`
      : role.grants.project ? "尚未绑定项目，不能单独派活" : "不访问"}</dd>
    <dt>执行框架</dt><dd>{role.engine.join("、")}</dd>
    <dt>单轮限制</dt><dd>{role.max_steps} 步 · {role.timeout_seconds} 秒</dd>
  </dl>;
}
export function StaffView({ state, onRefresh, onStatus, onDecide, onBudgets, onDevelopment }: {
  state: StaffState; onRefresh: () => void; onStatus: (member: StaffMember, status: StaffMember["status"]) => void;
  onDecide: (proposal: StaffProposal, approve: boolean) => void;
  onBudgets?: (settings: StaffBudgetSettings) => void;
  onDevelopment?: (data: DevelopmentProposal) => void;
}) {
  const { value, busy, error } = state;
  const disabled = busy || Boolean(error);
  return <div className="staff-pane">
    <div className="settings-card-head"><strong>专员与岗位</strong><button disabled={busy} onClick={onRefresh}>{busy ? "正在核对…" : "刷新名录"}</button></div>
    <p className="settings-card-note">在对话里请助理提议专员或部门。权限来自已批准岗位，派活不能临时加权限；需要确认的动作会等待主人审批。</p>
    {error && <p role="alert">{error}。当前显示上次读取的数据，刷新核对后才能继续操作。</p>}
    {!value && !error && <p role="status">{busy ? "正在读取常驻助理的名录…" : "尚未核对名录，请刷新。"}</p>}
    {value && <>
      {onDevelopment && <DevelopmentStaff value={value} disabled={disabled} onPropose={onDevelopment} />}
      {Boolean(value.standing_specialists?.length) && <details className="staff-section">
        <summary>查看三个常设专员的设置建议</summary>
        <div className="staff-section">
        <p className="settings-card-note">这些是登记建议，尚未开通。先在主要聊天说明汇报时间、时区和公开研究话题，再逐项核对并批准岗位、专员、部门和例行工作。</p>
        {value.standing_specialists?.map(item => <article className="settings-card" key={item.id}>
          <strong>{item.title}</strong><p>{item.scope}</p><p className="settings-card-note">{item.guide}</p>
        </article>)}
        <p className="settings-card-note">日程保持只读。原会前和新邮件提醒沿用现有规则，本步不改为实时提醒。</p>
        </div>
      </details>}
      {value.budget_settings && <StaffBudgets value={value.budget_settings} usage={value.usage} disabled={disabled} onSave={onBudgets} />}
      {value.proposals.some(p => ["pending", "applying"].includes(p.status)) && <section aria-label="待批登记" className="staff-section">
        <h3>待批登记</h3>
        {value.proposals.filter(p => ["pending", "applying"].includes(p.status)).map(proposal => {
          const role = proposal.snapshot.kind === "role" ? proposal.snapshot.data as unknown as StaffRole : proposal.snapshot.role_snapshot;
          return <article className="settings-card" key={proposal.id}>
            <strong>{proposal.snapshot.kind === 'routine_migration' ? '迁移旧定时事项' : String(proposal.snapshot.data.title ?? proposal.snapshot.data.name ?? "登记提议")}</strong>
            <p>{proposal.reason}</p>
            <p className="settings-card-note">{proposal.status === "applying" ? "已有主人批准记录，登记结果待核对；可核对后继续完成。" : "待你批准，尚未登记或授予权限。"}</p>
            {role && <><p>{role.brief}</p><RoleGrants role={role} /></>}
            {proposal.snapshot.members_snapshot?.map(member => <div key={member.id}><strong>{member.name}</strong><RoleGrants role={member.role_snapshot} /></div>)}
            <details><summary>提议原文与变更前记录</summary><pre className="staff-record">{JSON.stringify(proposal.snapshot, null, 2)}</pre></details>
            <p className="settings-card-note">核对权限和完整内容后，由你批准这一次登记。</p>
            <div className="settings-card-actions">
              <button disabled={disabled || proposal.status === "applying"} onClick={() => onDecide(proposal, false)}>拒绝</button>
              <button className="primary" disabled={disabled} onClick={() => onDecide(proposal, true)}>批准登记</button>
            </div>
          </article>;
        })}
      </section>}
      <section aria-label="已登记专员" className="staff-section">
        {!value.members.length ? <p>还没有专员。在对话里说“提议一个只读专员”，核对权限后再批准。</p> : value.members.map(member => {
          const role = value.roles.find(role => role.id === member.role);
          return <article className="settings-card" key={member.id}>
            <div className="settings-card-head"><strong>{member.name}</strong><span>{statuses[member.status]}{member.busy ? ` · ${member.busy} 件在办` : ""}</span></div>
            {role ? <><p>{role.title} · {role.brief}</p><RoleGrants role={role} /></> : <p role="alert">岗位不可读取，不能派活。</p>}
            <details><summary>最近做了什么 · {member.recent_tasks.length} 件</summary>
              {member.recent_tasks.length ? <ul>{member.recent_tasks.map(task => <li key={task.id}>{task.prompt || task.id} · {taskStatus[task.status] ?? "状态待核对"} · {task.review === "accepted" ? task.delivery === "review" ? "主人已验收" : "已核对汇报" : reviews[task.review] ?? "审查待核对"}</li>)}</ul> : <p>还没有任务记录。</p>}
            </details>
            {member.status !== "retired" && <div className="settings-card-actions">
              <button disabled={disabled} onClick={() => onStatus(member, member.status === "active" ? "paused" : "active")}>{member.status === "active" ? "暂停接活" : "恢复接活"}</button>
              <button disabled={disabled} onClick={() => onStatus(member, "retired")}>撤掉专员</button>
            </div>}
          </article>;
        })}
      </section>
      <details><summary>岗位登记 · {value.roles.length} 个</summary>{value.roles.map(role => <article className="settings-card" key={role.id}>
        <strong>{role.title} · {role.builtin ? "内置" : "自定义"}{!role.enabled ? " · 已停用" : ""}</strong><p>{role.brief}</p><RoleGrants role={role} />
      </article>)}</details>
      <p className="settings-card-note">暂停和撤掉会阻止新任务及排队任务继续执行；已经发出的只读请求可能仍会返回。撤掉保留记录，本批不支持恢复已撤掉的专员。</p>
    </>}
  </div>;
}

export function StaffPane({ projectId, client: injected }: { projectId?: string; client?: StaffClient & Partial<RoutinesClient> }) {
  const client = useMemo(() => injected ?? (projectId ? new GatewayClient({ baseUrl: "", token: "", remoteProjectId: projectId, expectedScope: "general" }) : null), [projectId, injected]);
  const [state, setState] = useState<StaffState>({ value: null, busy: false, error: "" });
  const controller = useRef<ReturnType<typeof staffController> | null>(null);
  useEffect(() => {
    setState({ value: null, busy: false, error: "" });
    if (!client) return;
    const current = staffController(client, setState);
    controller.current = current;
    void current.refresh();
    return () => { current.dispose(); if (controller.current === current) controller.current = null; };
  }, [client]);
  if (!client) return <p>请先在“电脑与连接”中登记常驻电脑。专员登记保存在常驻助理，不跟随本机项目切换。</p>;
  return <><StaffView state={state} onRefresh={() => void controller.current?.refresh()} onDecide={(proposal, approve) => void controller.current?.decide(proposal, approve)}
    onDevelopment={client.proposeDevelopment ? data => void controller.current?.proposeDevelopment(data) : undefined}
    onBudgets={settings => void controller.current?.budgets(settings)}
    onStatus={(member, status) => {
      const current = controller.current;
      if (status !== "retired") { void current?.status(member, status); return; }
      void confirmAction(`撤掉「${member.name}」？保留历史记录，但不能再接活，本批不能恢复。`)
        .then(ok => { if (ok && current === controller.current) void current?.status(member, status); })
        .catch(() => { if (current === controller.current) setState(value => ({ ...value, error: "确认窗口未完成，请刷新后重试" })); });
    }} />{client.readStaffRecovery && client.decideStaffRecovery && <StaffRecovery client={client as StaffClient & RecoveryClient} />}{client.readRoutines && client.changeRoutines
      ? <RoutinesPane client={client as StaffClient & RoutinesClient} onProposed={() => void controller.current?.refresh()} />
      : <p>当前连接尚不支持例行工作管理。</p>}</>;
}
