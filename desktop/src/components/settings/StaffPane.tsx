import { useEffect, useMemo, useRef, useState } from "react";
import { confirmAction } from "../../lib/confirm";
import { GatewayClient } from "../../gateway";
import { staffController, staffReadCurrent, type StaffClient, type StaffState, type StaffRole, type StaffMember, type StaffProposal, type StaffBudgetSettings } from "../../lib/staff";
import { StaffBudgets } from "./StaffBudgets";
import { ApprovalStatsPane } from './ApprovalStatsPane';
import type { ApprovalStatsClient } from '../../lib/approvalStats';
import { RoutinesPane } from './RoutinesPane';
import { DevelopmentStaff } from './DevelopmentStaff';
import { StaffRecovery } from './StaffRecovery';
import type { RecoveryClient } from '../../lib/staffRecovery';

import { StaffBundleReview } from './StaffBundleReview';
import type { DevelopmentProposal } from '../../lib/staff';
import type { RoutinesClient } from '../../lib/routines';
import { ExecutionDetails } from '../ExecutionDetails';
import { RoleAvatar, staffCharacter } from "../RoleAvatar";
import { Layers, ChartNoAxesColumn } from 'lucide-react';
import "./StaffPane.css";

const statuses = { active: "已启用", paused: "已暂停", retired: "已撤掉" };
const taskStatus: Record<string, string> = { queued: "待开始", running: "在忙", blocked: "等回答", done: "已交回", failed: "失败", interrupted: "中断", cancelled: "已取消" };
const reviews: Record<string, string> = { pending: "待核对", rework_requested: "待返工", not_submitted: "尚未交付" };
const operations: Record<string, string> = { read: "读取", write: "写文件", execute: "执行", own_data: "主人自己的数据", draft: "保存草稿（不发送）" };
const sourceNames: Record<string, string> = { memory: '长期记忆', goals: '个人目标', conversation: '对话检索',
  'plugin:mail': '邮箱', 'plugin:notion': 'Notion', 'plugin:calendar': '日历', 'plugin:caldav': 'CalDAV 日历' };

/** Presentation of known registration limits, never a dispatch authorization. */
function memberWarnings(member: StaffMember, role: StaffRole | undefined, state: StaffState) {
  if (!role) return ['岗位不可读取，不能派活。'];
  const warnings = [];
  if (!role.enabled) warnings.push('岗位已停用，不能派活。');
  if (!role.engine.includes(member.engine)) warnings.push('执行框架与岗位不符，请核对登记。');
  if (role.grants.project && !role.project_binding) warnings.push('岗位尚未绑定项目，不能单独派活。');
  if (role.grants.sees.some(source => source.startsWith('files:'))) warnings.push('登记目录来源尚未接入，不能派活。');
  if (member.department) {
    const department = state.value?.departments?.find(item => item.id === member.department);
    if (!department) warnings.push('所属部门状态待核对。');
    else if (department.status !== 'active') warnings.push('所属部门已暂停或撤掉，不能派活。');
  }
  return warnings;
}

function MemberScope({ role }: { role: StaffRole }) {
  const sources = role.grants.sees.map(source => sourceNames[source] ?? (source.startsWith('files:') ? '登记目录' : '其他已登记来源'));
  return <p className="staff-member-scope">
    <span>{sources.length ? `私人来源：${sources.join('、')}` : '无已登记私人来源'}</span>
    <span>{role.grants.web ? '允许查公开资料' : '不联网'}</span>
    {role.grants.project && <span>项目：{role.project || '待绑定'} · 一次性副本</span>}
    {role.grants.operations.some(operation => operation !== 'read') && <span>{role.grants.operations.map(operation => operations[operation] ?? operation).join('、')}</span>}
  </p>;
}
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
  const current = staffReadCurrent(state);
  const disabled = busy || !current;
  const proposals = value?.proposals.filter(proposal => ['pending', 'applying', 'cancelling'].includes(proposal.status)) ?? [];
  const checkedAt = state.checkedAt;
  const hasReadTime = typeof checkedAt === 'number' && Number.isFinite(checkedAt) && checkedAt >= 0 && checkedAt <= Date.now();
  return <div className="staff-pane">
    <div className="staff-directory-heading"><div><h3>专员名单</h3><p className="settings-card-note">{hasReadTime ? <>{current ? '本次' : '上次'}核对：<time dateTime={new Date(checkedAt).toISOString()}>{new Date(checkedAt).toLocaleString()}</time></> : '读取时间待核对'}</p></div>
      <button disabled={busy} onClick={onRefresh}>{busy ? "正在核对…" : "刷新名录"}</button></div>
    {error && <p role="alert">{error}。{value ? '当前显示上次读取的数据，刷新核对后才能继续操作。' : '尚未取得名录，请刷新后重试。'}</p>}
    {!error && value && !current && <p role="status" className="settings-card-note">名录已过期或读取时间无法确认，以下为上次读取的数据。请刷新核对后再操作。</p>}
    {!value && !error && <p role="status">{busy ? "正在读取常驻助理的名录…" : "尚未核对名录，请刷新。"}</p>}
    {value && <>
      <section aria-label="已登记专员" className="staff-roster">
        <h3>已登记专员 <span className="staff-count">{value.members.length}</span></h3>
        {!value.members.length ? <p className="staff-empty">还没有专员。在主要聊天里说“提议一个只读专员”，核对权限后再批准。</p> : value.members.map(member => {
          const role = value.roles.find(role => role.id === member.role);
          const warnings = memberWarnings(member, role, state);
          return <article className="staff-member" aria-label={member.name} key={member.id}>
            <div className="staff-member-overview"><RoleAvatar character={staffCharacter(role)} /><div className="staff-member-description"><strong>{member.name}</strong>
              {role && <p>{role.brief}</p>}
            </div><span className={`staff-member-status${current && member.status === 'active' && !warnings.length ? ' staff-member-enabled' : ''}`}>
              {!current && '上次：'}{statuses[member.status] ?? '状态待核对'}{member.busy ? ` · ${member.busy} 件在办` : ''}</span></div>
            {role && <MemberScope role={role} />}
            {warnings.map(warning => <p role="alert" key={warning}>{!current && '上次记录：'}{warning}</p>)}
            <details className="staff-member-details"><summary>查看详情</summary>
              {role && <><h4>{role.title}</h4><RoleGrants role={role} /></>}
              <details><summary>最近做了什么 · {member.recent_tasks.length} 件</summary>
                {member.recent_tasks.length ? <ul>{member.recent_tasks.map(task => <li key={task.id}>{task.prompt || task.id} · {!current && '上次：'}{taskStatus[task.status] ?? "状态待核对"} · {task.review === "accepted" ? task.delivery === "review" ? "主人已验收" : "已核对汇报" : reviews[task.review] ?? "审查待核对"}
                  <ExecutionDetails taskId={task.id} value={task.execution} current={current} />
                </li>)}</ul> : <p>还没有任务记录。</p>}
              </details>
              {member.status !== "retired" && <>
                <p className="settings-card-note">暂停或撤掉会阻止新任务与排队任务继续；已发出的只读请求可能仍会返回。撤掉后无法恢复，历史记录会保留。</p>
                <div className="settings-card-actions">
                  <button disabled={disabled} onClick={() => onStatus(member, member.status === "active" ? "paused" : "active")}>{member.status === "active" ? "暂停接活" : "恢复接活"}</button>
                  <button className="danger" disabled={disabled} onClick={() => onStatus(member, "retired")}>撤掉专员</button>
                </div>
              </>}
            </details>
          </article>;
        })}
        <p className="staff-roster-note">权限只来自你批准的岗位。已启用表示登记状态，接活时仍会核对岗位、部门和额度。</p>
      </section>
      {proposals.length > 0 && <section aria-label="待批登记" className="staff-section staff-proposals">
        <h3>待批登记 <span className="staff-count">{proposals.length}</span></h3>
        {proposals.map(proposal => {
          const role = proposal.snapshot.kind === "role" ? proposal.snapshot.data as unknown as StaffRole : proposal.snapshot.role_snapshot;
          return <article className="settings-card" key={proposal.id}>
            <strong>{proposal.snapshot.kind === 'bundle' ? '专员开通单' : proposal.snapshot.kind === 'routine_migration' ? '迁移旧定时事项' : String(proposal.snapshot.data.title ?? proposal.snapshot.data.name ?? "登记提议")}</strong>
            <p>{proposal.reason}</p>
            <p className="settings-card-note">{proposal.status === 'cancelling' ? '取消尚未完成，新登记仍未开放；请继续取消同一张单。' : proposal.status === "applying" ? "已有主人批准记录，登记结果待核对；可核对后继续完成。" : "待你批准，尚未登记或授予权限。"}</p>
            {proposal.snapshot.kind === 'bundle' && <StaffBundleReview proposal={proposal} renderRole={role => <RoleGrants role={role} />} />}
            {role && <><p>{role.brief}</p><RoleGrants role={role} /></>}
            {proposal.snapshot.members_snapshot?.map(member => <div key={member.id}><strong>{member.name}</strong><RoleGrants role={member.role_snapshot} /></div>)}
            <details><summary>提议原文与变更前记录</summary><pre className="staff-record">{JSON.stringify(proposal.snapshot, null, 2)}</pre></details>
            <p className="settings-card-note">核对权限和完整内容后，由你批准这一次登记。</p>
            <div className="settings-card-actions">
              <button disabled={disabled || (proposal.status === "applying" && proposal.snapshot.kind !== 'bundle')} onClick={() => onDecide(proposal, false)}>{proposal.status === 'cancelling' ? '继续取消开通单' : proposal.status === 'applying' ? '取消开通单' : '拒绝'}</button>
              <button className="primary" disabled={disabled || proposal.status === 'cancelling'} onClick={() => onDecide(proposal, true)}>{proposal.snapshot.kind === 'bundle' ? proposal.status === 'applying' ? '继续完成开通单' : '批准整张开通单' : '批准登记'}</button>
            </div>
          </article>;
        })}
      </section>}
      <div className="staff-settings-options">
      <details aria-label="岗位登记" className="staff-option"><summary><Layers aria-hidden="true" /><strong>岗位登记</strong><span>已登记 {value.roles.length} 个</span></summary>{value.roles.map(role => <article className="settings-card" key={role.id}>
        <strong>{role.title} · {role.builtin ? "内置" : "自定义"}{!role.enabled ? " · 已停用" : ""}</strong><p>{role.brief}</p><RoleGrants role={role} />
      </article>)}</details>
      {value.budget_settings && <div>
        {value.usage?.error ? <p className="staff-usage-note" role="alert">{value.usage.error}。用量待核对。</p>
          : <p className="staff-usage-note">{!current && '上次记录：'}{typeof value.usage?.total === 'number' && Number.isFinite(value.usage.total) && value.usage.total >= 0 ? `今天已记录 ${value.usage.total} token` : '用量尚未核对'}</p>}
        <details aria-label="使用额度" className="staff-option"><summary><ChartNoAxesColumn aria-hidden="true" /><strong>使用额度</strong><span>查看与调整</span></summary>
          <StaffBudgets value={value.budget_settings} usage={value.usage} disabled={disabled} onSave={onBudgets} />
        </details>
      </div>}
      </div>
      {onDevelopment && <DevelopmentStaff value={value} disabled={disabled} onPropose={onDevelopment} />}
      {Boolean(value.standing_specialists?.length) && <details className="staff-section">
        <summary>查看三个常设专员的设置建议</summary>
        <div className="staff-section">
        <p className="settings-card-note">这些是登记建议，尚未开通。先在主要聊天说明汇报时间、时区和公开研究话题，再核对整张开通单后批准。</p>
        {value.standing_specialists?.map(item => <article className="settings-card" key={item.id}>
          <div className="staff-suggestion-title"><RoleAvatar character={item.id === "calendar" ? "calendar" : item.id === "research" ? "research" : undefined} /><strong>{item.title}</strong></div><p>{item.scope}</p><p className="settings-card-note">{item.guide}</p>
        </article>)}
        <p className="settings-card-note">日程保持只读。原会前和新邮件提醒沿用现有规则，本步不改为实时提醒。</p>
        </div>
      </details>}
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
    const recheck = () => current.recheck();
    window.addEventListener('focus', recheck);
    document.addEventListener('visibilitychange', recheck);
    return () => {
      window.removeEventListener('focus', recheck);
      document.removeEventListener('visibilitychange', recheck);
      current.dispose(); if (controller.current === current) controller.current = null;
    };
  }, [client]);
  if (!client) return <p>请先在“电脑与连接”中登记常驻电脑。专员登记保存在常驻助理，不跟随本机项目切换。</p>;
  return <><StaffView state={state} onRefresh={() => void controller.current?.refresh()} onDecide={(proposal, approve) => void controller.current?.decide(proposal, approve)}
    onDevelopment={client.proposeDevelopment ? data => void controller.current?.proposeDevelopment(data) : undefined}
    onBudgets={settings => void controller.current?.budgets(settings)}
    onStatus={(member, status) => {
      const current = controller.current;
      if (status !== "retired") { void current?.status(member, status); return; }
      void confirmAction(`撤掉「${member.name}」？撤掉后无法恢复，历史记录会保留，但不能再接活。`)
        .then(ok => { if (ok && current === controller.current) void current?.status(member, status); })
        .catch(() => { if (current === controller.current) setState(value => ({ ...value, error: "确认窗口未完成，请刷新后重试" })); });
    }} />{client.readApprovalStats && <ApprovalStatsPane client={client as StaffClient & ApprovalStatsClient}
      names={Object.fromEntries([...(state.value?.members ?? []), ...(state.value?.routines ?? [])].map(item => [item.id, item.name]))} />}
    {client.readStaffRecovery && client.decideStaffRecovery && <StaffRecovery client={client as StaffClient & RecoveryClient} />}{client.readRoutines && client.changeRoutines
      ? <RoutinesPane client={client as StaffClient & RoutinesClient} onProposed={() => void controller.current?.refresh()} />
      : <p>当前连接尚不支持例行工作管理。</p>}</>;
}
