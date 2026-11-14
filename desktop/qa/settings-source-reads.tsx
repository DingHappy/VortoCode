/** Real hooks, synthetic deferred APIs. Never uses native IPC, files, models or a service. */
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { useTrustLevel } from "../src/hooks/useTrustLevel";
import { useExtensionsStatus } from "../src/hooks/useExtensionsStatus";
import type { GatewayClient } from "../src/gateway";
import type { TrustLevel, TrustStatus, HookConfigStatus, ExtensionsInspectSnapshot } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";

function Fixture() {
  const [host, setHost] = useState("A"), [notice, setNotice] = useState("");
  const waiting = useRef<(() => void)[]>([]), writes = useRef<string[]>([]);
  const [, setWriteRevision] = useState(0);
  const oldChange = useRef<(() => void) | null>(null);
  const trust = (level: TrustLevel): TrustStatus => ({ level, effective: level, ceiling: "reads", levels: ["ask", "reads"],
    capability_profile: "local", workspace_scope: "project", local_file_reads: true, supervised: true });
  const hooks = (name: string): HookConfigStatus => ({ configured: true, trusted: false, active: false, config_path: `/fixture/${name}`,
    config_sha256: name, hooks: [] });
  const extensions = (name: string): ExtensionsInspectSnapshot => ({ version: 1, summary: { total: 0, active: 0, attention: 0 }, items: [], issues: [name] });
  const later = <T,>(value: T) => new Promise<T>(resolve => waiting.current.push(() => resolve(value)));
  const [clients] = useState(() => Object.fromEntries(["A", "B"].map(name => [name, {
    connectionEpoch: 1,
    getTrust: () => name === "A" ? later(trust("ask")) : Promise.resolve(trust("reads")),
    setTrust: async (level: TrustLevel) => { writes.current.push(name); setWriteRevision(value => value + 1); return trust(level); },
    getHookStatus: () => name === "A" ? later(hooks(name)) : Promise.resolve(hooks(name)),
    getExtensionsInspect: () => name === "A" ? later(extensions(name)) : Promise.resolve(extensions(name)),
  }])) as unknown as Record<string, GatewayClient>);
  const clientRef = useRef(clients[host]); clientRef.current = clients[host];
  const level = useTrustLevel(clientRef, "connected", "/same-project", true, setNotice, host);
  const extension = useExtensionsStatus(clientRef, host);
  if (host === "B" && level.trust && !oldChange.current) oldChange.current = () => { void level.changeTrustLevel("ask"); };
  useEffect(() => { void extension.refreshHookStatus(); void extension.refreshExtensionsInspect(); }, [host, extension.refreshHookStatus, extension.refreshExtensionsInspect]);
  return <main style={{ padding: "var(--vc-space-6)" }}><h1>设置来源隔离验收</h1><p>真实 hooks；合成接口，不连接运行端。</p>
    <button onClick={() => setHost("B")}>切到 B</button>
    <button onClick={() => setHost("A")}>切回 A</button>
    <button onClick={() => { const queue = waiting.current.splice(0); queue.forEach(resolve => resolve()); }}>回传 A 的旧读取</button>
    <button onClick={() => void level.changeTrustLevel("ask")}>修改当前授权</button>
    <button onClick={() => oldChange.current?.()}>回调旧授权修改</button>
    <pre id="read-result">{JSON.stringify({ host, trust: level.trust?.level ?? null, hook: extension.hookStatus?.config_path ?? null,
      extensions: extension.extensionsInspect?.issues ?? null, busy: extension.extensionsInspectBusy, writes: writes.current })}</pre>
    <p role="status">{notice}</p>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
