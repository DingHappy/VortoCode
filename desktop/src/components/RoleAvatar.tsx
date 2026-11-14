import { UserRound } from "lucide-react";
import { avatarSrc } from "../lib/avatar";
import type { AssistantAvatar } from "../types";
import type { StaffRole } from "../lib/staff";
import general from "../assets/characters/general.png";
import research from "../assets/characters/research.png";
import development from "../assets/characters/development.png";
import calendar from "../assets/characters/calendar.png";
import "./RoleAvatar.css";

export const ROLE_ART = { general, research, development, calendar };
export type RoleCharacter = keyof typeof ROLE_ART;

/** Presentation only. Never infers permissions or enrollment from a name or picture. */
export function staffCharacter(role: StaffRole | undefined): RoleCharacter | undefined {
  if (!role) return undefined;
  if (role.project_binding && role.grants.project) return "development";
  if (!role.builtin) return undefined;
  if (["web-researcher", "public-researcher"].includes(role.id)) return "research";
  if (role.id === "developer") return "development";
  if (role.id === "calendar-reader") return "calendar";
  return undefined;
}

export function RoleAvatar({ character, avatar, size = "message", label }: {
  character?: RoleCharacter; avatar?: AssistantAvatar | null;
  size?: "small" | "message" | "portrait" | "hero"; label?: string;
}) {
  const custom = avatarSrc(avatar);
  const src = custom || (character ? ROLE_ART[character] : undefined);
  return <span className={`vc-role-avatar vc-role-avatar--${size}${custom ? " vc-role-avatar--custom" : ""}`}>
    {src ? <img src={src} alt={label ?? ""} /> : <UserRound aria-label={label} aria-hidden={label ? undefined : true} />}
  </span>;
}
