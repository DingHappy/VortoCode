import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import type { GatewayClient } from "../gateway";
import { createResourceReads, emptyResourceView, type ResourceSource, type ResourceView } from "../lib/sessionResources";
import type { ConnectionState } from "../types";

export function useSessionResources(clientRef: RefObject<GatewayClient | null>, session: string, connection: ConnectionState) {
  const source = useRef<ResourceSource<GatewayClient>>({ client: clientRef.current, session, connected: connection === "connected" });
  source.current = { client: clientRef.current, session, connected: connection === "connected" };
  const [saved, setSaved] = useState<{ source: ResourceSource<GatewayClient>; view: ResourceView } | null>(null);
  const [reader] = useState(() => createResourceReads(() => ({ ...source.current, client: clientRef.current, epoch: clientRef.current?.connectionEpoch }), (owner, view) => setSaved({ source: owner, view })));
  const client = clientRef.current;
  const epoch = client?.connectionEpoch;
  useEffect(() => { reader.reset(); return () => reader.dispose(); }, [client, session, epoch, reader]);
  useEffect(() => { if (connection !== "connected") reader.offline(); }, [connection, reader]);
  const view = saved?.source.client === client && saved?.source.session === session && saved?.source.epoch === epoch ? saved.view : emptyResourceView();
  return { view, refresh: useCallback(() => { void reader.load(); }, [reader]),
    receive: useCallback((raw: unknown) => reader.accept(raw), [reader]),
    changed: useCallback(() => reader.changed(), [reader]) };
}
