type ShortcutEvent = Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey" | "defaultPrevented" | "isComposing" | "keyCode">;

/** Editors and open dialogs own their keyboard input before global navigation. */
export function appShortcut(event: ShortcutEvent, dialogOpen: boolean): "search" | "settings" | null {
  if (dialogOpen || event.defaultPrevented || event.isComposing || event.keyCode === 229
    || event.altKey || event.shiftKey || !(event.metaKey || event.ctrlKey)) return null;
  if (event.key === ",") return "settings";
  return event.key.toLowerCase() === "k" ? "search" : null;
}
