import { useState } from 'react';
import type { DevelopmentProposal, StaffSnapshot } from '../../lib/staff';

export function DevelopmentStaff({ value, disabled, onPropose }: {
  value: StaffSnapshot; disabled: boolean; onPropose: (data: DevelopmentProposal) => void;
}) {
  const [project, setProject] = useState('');
  const [editing, setEditing] = useState('');
  const [title, setTitle] = useState('项目开发');
  const [role, setRole] = useState('');
  const [name, setName] = useState('开发专员');
  const roles = value.roles.filter(item => item.project_binding && item.enabled);
  const projects = value.development_projects ?? [];
  return <details className="staff-section">
    <summary>设置开发专员</summary>
    <div className="staff-section">
      <p className="settings-card-note">先提议并批准岗位，再提议并批准专员。每一步都由主人核对后确认；提交表单只留下待批提议，不会启动开发。</p>
      <p>只在固定项目的一次性副本里改代码，由系统验证并交回本地分支。不能自行运行命令、联网、推送、开 PR 或发布。</p>
      <form className="settings-card" aria-label="提议开发岗位" onSubmit={event => {
        event.preventDefault();
        if (!disabled && title.trim() && projects.some(item => item.name === project))
          onPropose({ kind: 'role', id: editing || 'dev-role-' + crypto.randomUUID().replace(/-/g, ''), name: title.trim(), project });
      }}>
        <h3>1. 提议开发岗位</h3>
        <label className="staff-field">岗位
          <select value={editing} disabled={disabled} onChange={event => {
            const id = event.target.value; setEditing(id);
            const current = roles.find(item => item.id === id);
            setTitle(current?.title ?? '项目开发'); setProject(current?.project ?? '');
          }}><option value="">新建岗位</option>{roles.map(item => <option key={item.id} value={item.id}>{item.title} · {item.project}</option>)}</select>
        </label>
        <label className="staff-field">岗位名称<input required maxLength={80} value={title} disabled={disabled} onChange={event => setTitle(event.target.value)} /></label>
        <label className="staff-field">固定项目<select required value={project} disabled={disabled} onChange={event => setProject(event.target.value)}>
          <option value="">选择已登记项目</option>{projects.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select></label>
        {!projects.length && <p className="settings-card-note">当前没有已登记项目，请先核对常驻电脑的项目登记。</p>}
        <p className="settings-card-note">换项目须重新批准岗位；原任务的授权不会转移。</p>
        <div className="settings-card-actions"><button type="submit" disabled={disabled || !title.trim() || !projects.some(item => item.name === project)}>提交岗位提议</button></div>
      </form>
      <form className="settings-card" aria-label="提议开发专员" onSubmit={event => {
        event.preventDefault();
        if (!disabled && name.trim() && roles.some(item => item.id === role))
          onPropose({ kind: 'member', id: 'dev-staff-' + crypto.randomUUID().replace(/-/g, ''), name: name.trim(), role });
      }}>
        <h3>2. 提议开发专员</h3>
        <label className="staff-field">已批准岗位<select required value={role} disabled={disabled} onChange={event => setRole(event.target.value)}>
          <option value="">选择固定项目岗位</option>{roles.map(item => <option key={item.id} value={item.id}>{item.title} · {item.project}</option>)}
        </select></label>
        <label className="staff-field">专员名称<input required maxLength={80} value={name} disabled={disabled} onChange={event => setName(event.target.value)} /></label>
        <div className="settings-card-actions"><button type="submit" disabled={disabled || !name.trim() || !roles.some(item => item.id === role)}>提交专员提议</button></div>
      </form>
    </div>
  </details>;
}
