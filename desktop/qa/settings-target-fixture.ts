import { useEffect, useMemo, useRef, useState } from "react";
import { desktopContext } from "../src/lib/desktopContext";
import { settingsTargetLease } from "../src/lib/settingsTarget";
import type { StaffClient } from "../src/lib/staff";
import type { WorkspaceScope } from "../src/types";
import { staffFixtureClient } from "./staff-fixture";

/** Synthetic source changes, including those that happen behind a native confirmation dialog. */
export function useSettingsTargetFixture(open: boolean, scope: WorkspaceScope, client: StaffClient = staffFixtureClient) {
  const [generation, setGeneration] = useState(1);
  const calls = useRef<{ generation: number; method: string }[]>([]);
  const context = desktopContext({ registrationId: `qa-host-${generation}`, assistantId: scope === "general" ? `qa-host-${generation}` : null,
    scope, repoRoot: scope === "project" ? "/fixture/project" : "", sid: "qa-chat", baseUrl: "https://assistant.example", runtimeId: "",
    connection: "connected", profiles: [{ id: `qa-host-${generation}`, kind: "remote", name: "演示助手", repoRoot: scope === "general" ? "@assistant" : "/fixture/project",
      baseUrl: "https://assistant.example", lastOpenedAt: 0 }], localResidentId: null,
    source: { registrationId: `qa-host-${generation}`, baseUrl: "https://assistant.example", sid: "qa-chat", epoch: 1, generation } });
  const transport = useMemo(() => new Proxy(client, { get(target, property) {
    const value = Reflect.get(target, property, target);
    return typeof value === "function" ? (...args: unknown[]) => {
      calls.current.push({ generation, method: String(property) }); return value.apply(target, args);
    } : value;
  } }), [client, generation]);
  const source = useRef({ context, client: transport }); source.current = { context, client: transport };
  const lease = useMemo(() => open ? settingsTargetLease(() => source.current) : undefined, [open]);
  useEffect(() => {
    Object.assign(window, { changeSettingsTarget: () => setGeneration(value => value + 1), settingsTargetCalls: () => [...calls.current] });
    return () => { Reflect.deleteProperty(window, "changeSettingsTarget"); Reflect.deleteProperty(window, "settingsTargetCalls"); };
  }, []);
  return lease;
}
