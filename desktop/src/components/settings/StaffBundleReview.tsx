import type { StaffProposal, StaffRole } from '../../lib/staff';
import type { ReactNode } from 'react';

export function StaffBundleReview({ proposal, renderRole }: { proposal: StaffProposal; renderRole: (role: StaffRole) => ReactNode }) {
  const entries = proposal.snapshot.entries ?? [];
  return <div className="staff-section" aria-label="整张开通单">
    <p>本次新建 {entries.filter(item => item.kind === 'role').length} 个岗位、{entries.filter(item => item.kind === 'member').length} 位专员、{entries.filter(item => item.kind === 'department').length} 个部门、{entries.filter(item => item.kind === 'routine').length} 条例行工作。全部完成后一起生效；只新建，不修改已有登记。</p>
    {entries.map(entry => {
      const data = entry.data;
      const role = entry.kind === 'role' ? data as unknown as StaffRole : entry.role_snapshot;
      return <article className="settings-card" key={`${entry.kind}:${String(data.id)}`}>
        <strong>{String(data.title ?? data.name ?? data.id)}</strong>
        {role && <><p>{role.brief}</p>{renderRole(role)}<p className="settings-card-note">常设放行：无。需要确认的动作仍逐次询问。</p></>}
        {entry.kind === 'member' && <p>岗位：{String(data.role)} · 执行框架：{String(data.engine)} · 模型：{String(data.model || '默认')}</p>}
        {entry.kind === 'department' && <><p>{String(data.duty)}</p><p>成员：{Array.isArray(data.members) ? data.members.map(String).join('、') : '待核对'} · 协调人：{String(data.lead || '无')}</p></>}
        {entry.members_snapshot?.map(member => <div key={member.id}><strong>{member.name}</strong>{renderRole(member.role_snapshot)}</div>)}
        {entry.kind === 'routine' && <>
          <p>排期：{String(data.schedule)} · 时区：{String(data.timezone)}</p>
          <p>专员：{String(data.member)} · {data.delivery === 'notify' ? '只告知' : '要主人验收'} · {data.enabled === false ? '不开启排期' : '批准整单后开启排期'}</p>
          <details><summary>完整任务与验收内容</summary><pre className="staff-record">{String(data.prompt)}{'\n'}{JSON.stringify(data.acceptance ?? [])}</pre></details>
        </>}
      </article>;
    })}
  </div>;
}
