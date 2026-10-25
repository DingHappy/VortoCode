/** Move through the modal's visible controls, including from a message with tabindex=-1. */
export function dialogTabTarget(controls: HTMLElement[], active: Element | null, backwards: boolean): HTMLElement | undefined {
  if (!controls.length) return undefined;
  const fallback = backwards ? controls[controls.length - 1] : controls[0];
  if (!active) return fallback;
  const index = controls.indexOf(active as HTMLElement);
  if (index >= 0) return controls[(index + (backwards ? -1 : 1) + controls.length) % controls.length];
  // DOM DOCUMENT_POSITION_PRECEDING=2, DOCUMENT_POSITION_FOLLOWING=4.
  const ordered = backwards ? [...controls].reverse() : controls;
  return ordered.find(control => active.compareDocumentPosition(control) & (backwards ? 2 : 4)) ?? fallback;
}
