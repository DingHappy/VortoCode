import { useLayoutEffect, useRef, useState } from "react";

/** Only explicit toggles move focus; opening the sidebar for search leaves it to the search input. */
export function useSidebarVisibility() {
  const [collapsed, setCollapsed] = useState(false);
  const collapseButton = useRef<HTMLButtonElement>(null);
  const reopenButton = useRef<HTMLButtonElement>(null);
  const focusPending = useRef(false);
  const toggle = (next: boolean) => {
    if (next === collapsed) return;
    focusPending.current = true;
    setCollapsed(next);
  };
  useLayoutEffect(() => {
    if (!focusPending.current) return;
    focusPending.current = false;
    (collapsed ? reopenButton : collapseButton).current?.focus({ preventScroll: true });
  }, [collapsed]);
  return { collapsed, setCollapsed, collapseButton, reopenButton, toggle };
}
