import type { ConnectionState, DesktopProjectProfile, WorkspaceScope } from "../types";
import { isAssistantProfile, personalSpaceRef } from "./personalSpace";

/** A client-side read identity, never a server instance or an authorization grant. */
export interface DesktopSource {
  registrationId: string | null;
  baseUrl: string;
  sid: string;
  generation: number;
  epoch: number;
}

export function retryAssistant(profiles: DesktopProjectProfile[], addressedId: string | null) {
  return profiles.find(profile => isAssistantProfile(profile) && !profile.assistantPending
    && (addressedId === null || profile.id === addressedId));
}

/** Read-only projection of the existing coordinator, runtime and SessionConnection owner. */
export function desktopContext(input: {
  registrationId: string | null; assistantId: string | null; scope: WorkspaceScope;
  repoRoot: string; sid: string; baseUrl: string; runtimeId: string; connection: ConnectionState;
  profiles: DesktopProjectProfile[]; localResidentId: string | null; source: DesktopSource | null;
}) {
  const { registrationId, assistantId, scope, sid, source } = input;
  const profile = input.profiles.find(item => item.id === registrationId);
  const assistant = Boolean(registrationId && registrationId === assistantId && scope === "general"
    && profile && isAssistantProfile(profile));
  const space = assistant ? personalSpaceRef(registrationId!)
    : !registrationId && scope === "general" ? personalSpaceRef() : null;
  const runtimeLabel = registrationId ? profile?.name || "已登记运行端" : "这台 Mac";
  const location = space ? assistant ? input.localResidentId === registrationId ? "本机常驻 · 个人空间" : "已登记助手 · 个人空间"
    : "临时聊天 · 个人空间" : scope === "scratch" ? "临时工作区" : "项目 · 空间归属待核对";
  // Local engine ports can change during preparation; an edited remote address is a different target.
  const targetKey = JSON.stringify([registrationId, registrationId ? input.baseUrl : "", scope, input.repoRoot]);
  const draftKey = JSON.stringify([targetKey, sid]);
  const ready = input.connection === "connected" && Boolean(source && source.registrationId === registrationId
    && source.baseUrl === input.baseUrl && source.sid === sid);
  return { space, runtimeLabel, location, targetKey, draftKey, sid, ready,
    projectRoot: scope === "project" ? input.repoRoot : null,
    // A→B→A, reconnecting the same client, and replacing a client all invalidate prior reads.
    readKey: JSON.stringify([targetKey, sid, input.baseUrl, input.runtimeId, input.connection,
      source?.generation, source?.epoch]),
  };
}

export type DesktopContext = ReturnType<typeof desktopContext>;
