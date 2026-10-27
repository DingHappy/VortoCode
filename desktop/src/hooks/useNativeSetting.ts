import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { errorText } from "../lib/errorText";

/** Read-only loading and retry. Writes and native consent remain with the caller. */
export function useNativeSetting<T>(command: string, failure: string, onRead?: (value: T) => void) {
  const [value, setValue] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);
  const readCallback = useRef(onRead);
  readCallback.current = onRead;
  const reload = useCallback(() => setAttempt(previous => previous + 1), []);
  const replace = useCallback((next: T) => {
    ++generation.current;
    setValue(next); setLoading(false); setError("");
  }, []);
  useEffect(() => {
    let alive = true;
    const request = ++generation.current;
    const current = () => alive && generation.current === request;
    setLoading(true); setError("");
    void invoke<T>(command).then(next => {
      if (!current()) return;
      readCallback.current?.(next);
      setValue(next);
    }).catch(cause => { if (current()) setError(errorText(cause, failure)); })
      .finally(() => { if (current()) setLoading(false); });
    return () => { alive = false; };
  }, [command, failure, attempt]);
  return { value, loading, error, reload, replace };
}
