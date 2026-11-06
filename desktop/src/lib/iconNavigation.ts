/** Horizontal tab bars have one tab stop; arrows wrap, Home/End select an edge. */
export function tabKeyTarget(key: string, index: number, count: number): number | null {
  if (count < 1 || index < 0 || index >= count) return null;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  if (key === "ArrowLeft") return (index + count - 1) % count;
  if (key === "ArrowRight") return (index + 1) % count;
  return null;
}

/** Keep short button hints inside the viewport, flipping above when necessary. */
export function hintPosition(anchor: { left: number; right: number; top: number; bottom: number },
  box: { width: number; height: number }, viewport: { width: number; height: number }) {
  const gap = 8;
  const clamp = (value: number, size: number, limit: number) => Math.max(gap, Math.min(value, limit - size - gap));
  const below = anchor.bottom + gap;
  return { left: clamp((anchor.left + anchor.right - box.width) / 2, box.width, viewport.width),
    top: clamp(below + box.height + gap <= viewport.height ? below : anchor.top - box.height - gap, box.height, viewport.height) };
}
