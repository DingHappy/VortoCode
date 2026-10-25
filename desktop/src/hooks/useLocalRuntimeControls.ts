import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { confirmAction } from "../lib/confirm";
import { errorText } from "../lib/errorText";
import { runtimeControlTarget, withIdleRuntime } from "../lib/runtimeLifecycle";
import type { ManagedRuntimes } from "../lib/managedRuntimes";
import type { GatewayProcessStatus, RuntimeSnapshot, WorkspaceScope } from "../types";

export type RuntimeStart = { scope: WorkspaceScope; repoRoot?: string; workspaceId?: string; baseUrl: string; sid: string; token: string; announce?: boolean };

type Context = {
  processStatus: GatewayProcessStatus; scope: WorkspaceScope; runtime: RuntimeSnapshot;
  repoRoot: string; baseUrl: string; sid: string; token: string;
  remote: () => boolean; starting: () => boolean; switching: () => boolean; client: () => unknown;
  runtimes: ManagedRuntimes; disconnect: () => Promise<void>;
  startWorkspace: (request: RuntimeStart) => Promise<boolean>;
  readActivity: (status: GatewayProcessStatus, token: string) => Promise<unknown>;
  onBanner: (message: string) => void;
};

/** Current-local-runtime controls; no task store, automatic retry or remote stop. */
export function useLocalRuntimeControls(context: Context) {
  const { processStatus, scope: activeScope, runtime, repoRoot, baseUrl, sid: activeSid, token,
    runtimes, disconnect, startWorkspace, onBanner: setBanner } = context;
  const [runtimeActionBusy, setRuntimeActionBusy] = useState(false);
  const [runtimeActionNote, setRuntimeActionNote] = useState("");
  const runtimeActionRef = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const stoppedLocalRuntimeRef = useRef<GatewayProcessStatus | null>(null);
  const runtimeControlStatus = runtimeControlTarget(processStatus, stoppedLocalRuntimeRef.current,
    { ...runtime, scope: activeScope }, context.remote());
  const localRuntimeAction = async <T,>(work: () => Promise<T>): Promise<T> => {
    if (!alive.current || runtimeActionRef.current || context.starting() || context.switching()) {
      throw new Error("已有工作区或引擎操作正在进行，请等待完成后重试");
    }
    runtimeActionRef.current = true; setRuntimeActionBusy(true); setRuntimeActionNote("");
    try { return await work(); }
    catch (error) { if (alive.current) setRuntimeActionNote(errorText(error, "本机引擎操作未完成")); throw error; }
    finally { runtimeActionRef.current = false; if (alive.current) setRuntimeActionBusy(false); }
  };

  const startRuntime = async () => {
    try {
      await localRuntimeAction(async () => {
        if (context.remote()) throw new Error("当前是远端会话，请先切换到本机通用工作区");
        const target = runtimeControlStatus;
        const scope = target.scope ?? activeScope;
        const started = await startWorkspace({
          scope, repoRoot: scope === "project" ? target.repoRoot || repoRoot.trim() : undefined,
          workspaceId: scope === "scratch" ? target.workspaceId || activeSid : undefined,
          baseUrl: target.baseUrl || baseUrl, sid: activeSid, token,
        });
        if (!started) throw new Error("本机引擎未能就绪，请查看连接诊断后重试");
        stoppedLocalRuntimeRef.current = null;
        setRuntimeActionNote("本机引擎已就绪"); setBanner("本机引擎已就绪");
      });
    } catch (error) { setBanner(errorText(error, "启动本机引擎失败")); }
  };

  const changeIdleRuntime = async (restart: boolean, ask: boolean): Promise<boolean> => {
    if (context.remote()) throw new Error("当前连接的是远端实例，本机入口不会停止或重启服务器");
    const expected = processStatus;
    if (!expected.runtimeId || !expected.running) return false;
    const previous = { scope: expected.scope ?? activeScope, workspaceId: expected.workspaceId, repoRoot: expected.repoRoot || "",
      baseUrl: expected.baseUrl || baseUrl, sid: activeSid, token: runtimes.tokenFor(expected.runtimeId) || token };
    const client = context.client();
    const generation = runtimes.currentGeneration();
    return withIdleRuntime({
      expected,
      current: () => alive.current && !context.remote() && generation === runtimes.currentGeneration() && context.client() === client,
      readProcess: () => invoke<GatewayProcessStatus>("gateway_process_status", { runtimeId: expected.runtimeId }),
      readActivity: status => context.readActivity(status, previous.token),
      confirm: ask ? status => confirmAction(`${restart ? "重新加载" : "停止"}这台 Mac 的${status.scope === "general" ? "通用" : status.scope === "scratch" ? "隔离" : "项目"}引擎？\n工作区：${status.workspaceRoot}\n不会操作常驻服务器。`) : undefined,
      execute: async status => {
        runtimes.invalidate();
        const stopGeneration = runtimes.currentGeneration();
        await disconnect();
        if (!alive.current || context.remote() || runtimes.currentGeneration() !== stopGeneration) {
          throw new Error("连接已切换，本次引擎操作已取消");
        }
        if (restart) {
          await runtimes.stopForRestart(status.runtimeId!, status.launchId!);
          stoppedLocalRuntimeRef.current = { ...status, running: false, pid: undefined, launchId: undefined };
          if (!alive.current || context.remote() || runtimes.currentGeneration() !== stopGeneration) throw new Error("工作区已切换，未启动新的引擎");
          const started = await startWorkspace({
            scope: previous.scope, repoRoot: previous.scope === "project" ? previous.repoRoot : undefined,
            workspaceId: previous.scope === "scratch" ? previous.workspaceId || previous.sid : undefined,
            baseUrl: previous.baseUrl, sid: previous.sid, token: previous.token, announce: false,
          });
          if (!started) throw new Error("本机引擎未能重新启动，请通过启动入口恢复");
          stoppedLocalRuntimeRef.current = null;
        } else {
          await runtimes.stopCurrent(status.runtimeId!, status.launchId!);
          stoppedLocalRuntimeRef.current = { ...status, running: false, pid: undefined, launchId: undefined };
        }
        runtimes.invalidate();
      },
    });
  };

  const stopRuntime = async () => {
    try {
      if (await localRuntimeAction(() => changeIdleRuntime(false, true))) { setRuntimeActionNote("本机引擎已停止，可在同一入口重新启动"); setBanner("本机引擎已停止，可在同一入口重新启动"); }
    } catch (error) { setBanner(errorText(error, "停止本机引擎失败")); }
  };

  const reloadRuntime = async () => {
    try {
      if (await localRuntimeAction(() => changeIdleRuntime(true, true))) { setRuntimeActionNote("本机引擎已重新加载，请核对模型配置生效状态"); setBanner("本机引擎已重新加载，请核对模型配置生效状态"); }
    } catch (error) { setBanner(errorText(error, "重新加载本机引擎失败")); }
  };

  const restartCurrentRuntimeForLlmProfile = async (): Promise<boolean> => {
    // Saving this Mac's settings never restarts the resident server.
    if (context.remote() || !processStatus.runtimeId || !processStatus.running) return false;
    return localRuntimeAction(() => changeIdleRuntime(true, false));
  };
  return { target: runtimeControlStatus, busy: runtimeActionBusy, note: runtimeActionNote,
    isOperating: () => runtimeActionRef.current, start: startRuntime, stop: stopRuntime,
    reload: reloadRuntime, restartIfRunning: restartCurrentRuntimeForLlmProfile };
}
