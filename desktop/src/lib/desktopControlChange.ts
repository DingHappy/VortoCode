import type { DesktopControlStatus } from "../types";
export type DesktopControlIntent = "toggle" | "save";

/** Turning off uses the last saved values; an invalid unsaved app list cannot block revocation. */
export function desktopControlChange(status: DesktopControlStatus, draft: { target: string; apps: string }, intent: DesktopControlIntent) {
  const keepDraft = intent === "toggle" && status.enabled;
  return {
    args: { enabled: intent === "toggle" ? !status.enabled : status.enabled,
      target: keepDraft ? status.target : draft.target, apps: keepDraft ? status.apps : draft.apps },
    keepDraft,
    restart: intent === "toggle" || status.enabled,
  };
}
