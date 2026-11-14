import type { DesktopSource } from "../lib/desktopContext";

/** Address selection for a reconnect; explicit user selections always win. */
export function connectionAddress(explicit: string | undefined, selected: string | null,
  fallback: string, attached: DesktopSource | null): string {
  return explicit ?? (selected && attached?.registrationId === selected ? attached.baseUrl : fallback);
}
