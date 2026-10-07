import { useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";

/** A keyed task form owns its draft; this hook owns in-flight request isolation. */
export function useTaskAction(client: GatewayClient | null, getClient: () => GatewayClient | null, onUpdated: () => void,
  options: { repeatable?: boolean } = {}) {
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<object | null>(null);
  const current = useRef({ client, getClient, onUpdated });
  current.current = { client, getClient, onUpdated };
  useEffect(() => {
    setBusy(false); setSaved(false); setError("");
    return () => { pending.current = null; };
  }, [client]);

  async function run(operation: (client: GatewayClient, isCurrent: () => boolean) => Promise<void>) {
    if (!client || current.current.getClient() !== client || pending.current || saved) return;
    const attempt = {};
    pending.current = attempt;
    const isCurrent = () => pending.current === attempt && current.current.client === client
      && current.current.getClient() === client;
    setBusy(true); setError("");
    try {
      await operation(client, isCurrent);
      if (!isCurrent()) return;
      if (!options.repeatable) setSaved(true);
      current.current.onUpdated();
    } catch (cause) {
      if (isCurrent()) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (isCurrent()) { pending.current = null; setBusy(false); }
    }
  }
  return { busy, saved, error, run };
}
