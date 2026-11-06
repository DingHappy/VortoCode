import { useEffect, useId, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes } from "react";
import { createPortal } from "react-dom";
import { hintPosition } from "../../lib/iconNavigation";

/** A button stays a single layout item. Its hint also works with keyboard focus and native dialogs. */
export function HintButton({ hint, type = "button", onPointerEnter, onPointerLeave, onFocus, onBlur, onClick,
  "aria-describedby": description, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { hint: string }) {
  const anchor = useRef<HTMLButtonElement>(null), popup = useRef<HTMLDivElement>(null), id = useId();
  const [open, setOpen] = useState(false), [position, setPosition] = useState({ left: 0, top: 0 });
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const hovered = useRef(false), focused = useRef(false);
  const stopTimer = () => { clearTimeout(timer.current); timer.current = undefined; };
  const dismiss = () => { stopTimer(); setOpen(false); };
  const leave = () => { stopTimer(); timer.current = setTimeout(() => { if (!hovered.current && !focused.current) setOpen(false); }, 120); };
  useEffect(() => () => { clearTimeout(timer.current); }, []);
  useLayoutEffect(() => {
    if (!open || !anchor.current || !popup.current) return;
    setPosition(hintPosition(anchor.current.getBoundingClientRect(), popup.current.getBoundingClientRect(),
      { width: window.innerWidth, height: window.innerHeight }));
  }, [open, hint]);
  useEffect(() => {
    if (!open) return;
    const key = (event: KeyboardEvent) => { if (event.key === "Escape" && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault(); event.stopPropagation(); dismiss();
    } };
    const moved = () => dismiss();
    document.addEventListener("keydown", key, true);
    window.addEventListener("resize", moved); window.addEventListener("scroll", moved, true);
    return () => { document.removeEventListener("keydown", key, true); window.removeEventListener("resize", moved); window.removeEventListener("scroll", moved, true); };
  }, [open]);
  return <><button {...props} ref={anchor} type={type} data-hint={hint}
    aria-describedby={[description, open ? id : ""].filter(Boolean).join(" ") || undefined}
    onPointerEnter={event => { onPointerEnter?.(event); hovered.current = true; stopTimer(); timer.current = setTimeout(() => setOpen(true), 300); }}
    onPointerLeave={event => { onPointerLeave?.(event); hovered.current = false; leave(); }}
    onFocus={event => { onFocus?.(event); focused.current = true; stopTimer(); setOpen(true); }}
    onBlur={event => { onBlur?.(event); focused.current = false; leave(); }}
    onClick={event => { dismiss(); onClick?.(event); }} />
    {open && createPortal(<div ref={popup} id={id} role="tooltip" className="vc-button-hint" style={position}
      onPointerEnter={() => { hovered.current = true; stopTimer(); }} onPointerLeave={() => { hovered.current = false; leave(); }}>{hint}</div>,
      anchor.current?.closest("dialog[open]") ?? document.body)}</>;
}
