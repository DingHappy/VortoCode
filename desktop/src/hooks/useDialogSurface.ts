import { useEffect, useRef } from "react";
import { dialogTabTarget } from "../lib/dialogFocus";

/** Share native modal focus ownership across activity and read-only phone views. */
export function useDialogSurface() {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    const activity = previous instanceof HTMLElement ? previous.closest(".assistant-activity") : null;
    const node = dialog.current;
    const containFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || event.ctrlKey || event.altKey || event.metaKey || !node?.open) return;
      const controls = [...node.querySelectorAll<HTMLElement>('button,a[href],input,select,textarea,summary,[tabindex]')]
        .filter(item => item.tabIndex >= 0 && !item.matches(':disabled') && !item.closest('[hidden],[inert]')
          && item.getClientRects().length > 0 && getComputedStyle(item).visibility !== "hidden");
      event.preventDefault();
      (dialogTabTarget(controls, document.activeElement, event.shiftKey) ?? node).focus();
    };
    node?.addEventListener("keydown", containFocus);
    node?.showModal();
    return () => {
      node?.removeEventListener("keydown", containFocus);
      const ownedFocus = document.activeElement === document.body || !!node?.contains(document.activeElement);
      node?.close();
      if (!ownedFocus || document.querySelector("dialog[open]")) return;
      const target = previous instanceof HTMLElement && previous.isConnected ? previous
        : activity?.isConnected ? activity.querySelector<HTMLElement>('.assistant-activity-filters button[aria-pressed="true"]') : null;
      target?.focus({ preventScroll: true });
    };
  }, []);
  return dialog;
}
