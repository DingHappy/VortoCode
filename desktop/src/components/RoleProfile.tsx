import { useId, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import type { AssistantAvatar } from "../types";
import { COPY } from "../lib/copy.generated";
import { tabKeyTarget } from "../lib/iconNavigation";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { RoleAvatar, type RoleCharacter } from "./RoleAvatar";
import { IconButton } from "./ui";
import "./RoleProfile.css";

export type RoleProfileTab = "settings" | "access" | "memory" | "activity";
export interface RoleProfileSection { id: RoleProfileTab; label: string; content: ReactNode }
export interface RoleProfileProps {
  name: string; description: string; character?: RoleCharacter; avatar?: AssistantAvatar | null;
  sections: RoleProfileSection[]; current: boolean; onClose: () => void;
}

/** Same profile surface for the main assistant and registered workers. All writes remain caller-owned. */
export function RoleProfileView({ name, description, character, avatar, sections, current, onClose }: RoleProfileProps) {
  const [selected, setSelected] = useState(sections[0].id);
  const tabs = useRef<Array<HTMLButtonElement | null>>([]), id = useId();
  const active = sections.some(section => section.id === selected) ? selected : sections[0].id;
  return <>
    <header className="vc-role-profile-heading"><RoleAvatar character={character} avatar={avatar} size="portrait" />
      <div><h1 id={`${id}-title`}>{name}</h1><p>{description}</p></div>
      <IconButton label={COPY.role.close} icon={<X aria-hidden="true" />} onClick={onClose} /></header>
    <div className="vc-role-profile-tabs" role="tablist" aria-label={COPY.role.profile} onKeyDown={event => {
      const at = sections.findIndex(section => section.id === active);
      const next = tabKeyTarget(event.key, at, sections.length);
      if (next === null) return;
      event.preventDefault(); setSelected(sections[next].id); tabs.current[next]?.focus();
    }}>{sections.map((section, index) => <button key={section.id} ref={node => { tabs.current[index] = node; }} role="tab"
      id={`${id}-${section.id}`} aria-selected={active === section.id} aria-controls={`${id}-${section.id}-panel`}
      tabIndex={active === section.id ? 0 : -1} onClick={() => setSelected(section.id)}>{section.label}</button>)}</div>
    {!current && <p className="vc-role-profile-warning" role="status">{COPY.role.source_changed}</p>}
    {sections.map(section => <section key={section.id} id={`${id}-${section.id}-panel`} role="tabpanel"
      aria-labelledby={`${id}-${section.id}`} hidden={active !== section.id} tabIndex={0}>
      <fieldset disabled={!current} className="vc-role-profile-fields">{section.content}</fieldset>
    </section>)}
  </>;
}

export function RoleProfileDialog(props: RoleProfileProps) {
  const dialog = useDialogSurface();
  return <dialog ref={dialog} className="vc-role-profile-dialog" aria-label={`${COPY.role.profile}：${props.name}`}
    onCancel={event => { event.preventDefault(); props.onClose(); }}><RoleProfileView {...props} /></dialog>;
}
