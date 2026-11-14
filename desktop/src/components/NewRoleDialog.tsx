import { useState } from "react";
import { X } from "lucide-react";
import type { StaffRole } from "../lib/staff";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { Button, IconButton } from "./ui";
import { COPY } from "../lib/copy.generated";
import "./RoleProfile.css";

export function roleProposalDraft(name: string, duty: string, role?: StaffRole) {
  return `请提议添加角色“${name.trim()}”。\n分工：${duty.trim()}\n${role ? `先核对已登记的权限方案“${role.title}”（${role.id}，修订 ${role.revision}）。` : "请先提议满足这项分工的最小权限方案。"}\n请先展示完整提议，由我核对后确认；现在不要派发任务。`;
}

/** Produces an owner-visible draft only. Registration and authority stay with existing proposal approval. */
export function NewRoleDialog({ roles, current, onPrepare, onClose }: {
  roles: StaffRole[]; current: () => boolean; onPrepare: (draft: string) => void; onClose: () => void;
}) {
  const dialog = useDialogSurface();
  const [name, setName] = useState(""), [duty, setDuty] = useState(""), [roleId, setRoleId] = useState("");
  const [error, setError] = useState("");
  return <dialog ref={dialog} className="vc-new-role-dialog" aria-labelledby="new-role-title"
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <header><h2 id="new-role-title">{COPY.role.add}</h2><IconButton label="关闭添加角色" icon={<X aria-hidden="true" />} onClick={onClose} /></header>
    <p>先说明分工，准备一条对话草稿。发送后再核对完整提议，批准前不会添加角色或派活。</p>
    <form onSubmit={event => {
      event.preventDefault();
      if (!name.trim() || !duty.trim()) return;
      if (!current()) { setError(COPY.role.source_changed); return; }
      const role = roles.find(role => role.id === roleId && role.enabled);
      if (roleId && !role) { setError("权限方案不可用，请关闭后重新核对。"); return; }
      onPrepare(roleProposalDraft(name, duty, role));
    }}>
      <label>名字<input autoFocus value={name} maxLength={80} onChange={event => setName(event.target.value)} placeholder="例如：研究助手" required /></label>
      <label>它负责什么<textarea value={duty} maxLength={3000} onChange={event => setDuty(event.target.value)} placeholder="说明任务、资料来源和交回什么结果" required /></label>
      <label>权限方案<select value={roleId} onChange={event => setRoleId(event.target.value)}>
        <option value="">先让总助手提议</option>{roles.filter(role => role.enabled).map(role => <option key={role.id} value={role.id}>{role.title}</option>)}
      </select></label>
      {error && <p role="alert">{error}</p>}
      <footer><Button onClick={onClose}>取消</Button><Button type="submit" variant="primary" disabled={!name.trim() || !duty.trim()}>放入当前对话</Button></footer>
    </form>
  </dialog>;
}
