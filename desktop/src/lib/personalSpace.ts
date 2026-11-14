// Read-only compatibility projection. Registration identity is not runtime identity or authority.
import type { DesktopProjectProfile } from "../types";
import { STORAGE_KEYS } from "./storage";

export const ASSISTANT_HOST_ROOT = "@assistant";

export function isAssistantProfile(profile: Pick<DesktopProjectProfile, "kind" | "repoRoot">): boolean {
  return profile.kind === "remote" && profile.repoRoot === ASSISTANT_HOST_ROOT;
}

export interface PersonalSpaceRef {
  readonly id: string;
  readonly runtime: {
    readonly kind: "local-engine" | "registered-assistant";
    readonly registrationId: string | null;
  };
  readonly mainChatKey: string;
}

/** One product-space reference per registered assistant; preserve every existing chat storage key. */
export function personalSpaceRef(registrationId?: string | null): PersonalSpaceRef {
  return {
    id: registrationId ? `desktop:assistant:${registrationId}:personal` : "desktop:local-engine:personal",
    runtime: {
      kind: registrationId ? "registered-assistant" : "local-engine",
      registrationId: registrationId || null,
    },
    mainChatKey: registrationId ? `${STORAGE_KEYS.mainChatSid}:${registrationId}` : STORAGE_KEYS.mainChatSid,
  };
}

/**
 * The legacy registry does not persist a project's parent assistant. Keep that relationship unknown,
 * even for equal URLs or directory names. Pending assistants are neither selectable spaces nor projects.
 */
export function personalSpaceDirectory(profiles: readonly DesktopProjectProfile[]): {
  spaces: Array<PersonalSpaceRef & { profile?: DesktopProjectProfile }>;
  projects: Array<{ registrationId: string; personalSpaceId: null; profile: DesktopProjectProfile }>;
} {
  return {
    spaces: [
      personalSpaceRef(),
      ...profiles.filter(p => isAssistantProfile(p) && !p.assistantPending)
        .map(profile => ({ ...personalSpaceRef(profile.id), profile })),
    ],
    projects: profiles.filter(p => !isAssistantProfile(p))
      .map(profile => ({ registrationId: profile.id, personalSpaceId: null, profile })),
  };
}
