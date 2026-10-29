import { useEffect, type RefObject } from "react";
import { stickToBottom } from "../lib/stickToBottom";

/** 人在底部时，内容怎么变高都跟到底。mountKey 变化（容器重新挂载）时重新接上。 */
export function useStickToBottom(container: RefObject<HTMLElement | null>, autoScroll: RefObject<boolean>, mountKey: unknown) {
  useEffect(() => {
    const node = container.current;
    if (!node) return;
    return stickToBottom(node, () => Boolean(autoScroll.current));
  }, [container, autoScroll, mountKey]);
}
