import type { ReactNode } from "react";
import { ArrowLeft, Settings2 } from "lucide-react";
import type { AssistantAvatar } from "../types";
import { COPY } from "../lib/copy.generated";
import { RoleAvatar, type RoleCharacter } from "./RoleAvatar";
import { Button } from "./ui";
import "./StaffWorkspace.css";

/** The gallery describes visual identities only; the original registry owns all permissions. */
export function RoleFamily({ avatar }: { avatar?: AssistantAvatar | null }) {
  return <section className="vc-role-family" aria-labelledby="role-family-heading">
    <div><h2 id="role-family-heading">{COPY.characters.family}</h2><p>{COPY.characters.family_hint}</p></div>
    <div className="vc-role-family-grid">{(["general", "research", "development", "calendar"] as RoleCharacter[]).map(character => <figure key={character}>
      <RoleAvatar character={character} avatar={character === "general" ? avatar : undefined} size="portrait" />
      <figcaption>{COPY.characters[character]}</figcaption>
    </figure>)}</div>
  </section>;
}

export function AssistantIdentityLink({ name, avatar, onOpen }: { name: string; avatar?: AssistantAvatar | null; onOpen: () => void }) {
  return <div className="vc-assistant-link-group"><span>{COPY.characters.general}</span>
    <button className="vc-assistant-link" onClick={onOpen}><RoleAvatar character="general" avatar={avatar} />
      <span><strong>{name}</strong><small>身份与记忆</small></span></button></div>;
}

export function StaffWorkspace({ avatar, onBack, onSettings, children }: {
  avatar?: AssistantAvatar | null; onBack: () => void; onSettings: () => void; children: ReactNode;
}) {
  return <section className="vc-staff-workspace" aria-label={COPY.characters.workspace}>
    <header className="vc-staff-workspace-header"><div><Button variant="text" icon={<ArrowLeft aria-hidden="true" />} onClick={onBack}>{COPY.characters.open_chat}</Button>
      <h1>{COPY.characters.workspace}</h1><p>{COPY.characters.workspace_hint}</p></div>
      <Button icon={<Settings2 aria-hidden="true" />} onClick={onSettings}>{COPY.characters.manage_connection}</Button></header>
    <RoleFamily avatar={avatar} />
    <section className="vc-staff-workspace-registry" aria-label={COPY.characters.roster}>{children}</section>
  </section>;
}
