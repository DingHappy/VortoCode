import { useEffect, useState } from "react";

/** value 持续为 true 超过 delayMs 才返回 true；变回 false 立即返回 false。用来避免一闪而过的加载提示。 */
export function useDelayedFlag(value: boolean, delayMs: number): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!value) {
      setShown(false);
      return;
    }
    const timer = window.setTimeout(() => setShown(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return value && shown;
}
