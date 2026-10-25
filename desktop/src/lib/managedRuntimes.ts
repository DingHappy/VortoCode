// Desktop 托管的 runtime 进程：启动/停止、恢复记录、各 runtime 的连接令牌，以及 2 秒一轮的监督。
//
// 只经 Tauri 命令读写进程，不碰 WebSocket 会话（那归 SessionConnection 与 App 的连接代码）。
// 界面状态由注入的 sink 写回（React state 仍在 hook 里）；本类只持有三样不进渲染的东西：
// - running：上一轮看到当前 runtime 在跑没有，用来区分「意外退出」和「自己点了停止」；
// - generation：监督代次，任何启动/停止/切换前后各加一，进行中的监督读取回来对不上就丢弃；
// - tokens：每个 runtime 的连接令牌（进程退出就删，不留给同端口新起的进程）。
import type { DesktopProjectProfile, GatewayProcessStatus, GatewayRecoveryRecord } from "../types";
import { EMPTY_PROCESS, observeCurrentRuntime, pruneRuntimeTokens } from "./runtimeSupervision";
import { remoteProcessStatus } from "./workspaceRouting";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export interface ManagedRuntimeSink {
  setProcessStatus: (status: GatewayProcessStatus) => void;
  setRuntimeProcesses: (items: GatewayProcessStatus[]) => void;
  setRuntimeRecoveries: (items: GatewayRecoveryRecord[]) => void;
  setRecoveryRecord: (record: GatewayRecoveryRecord | null) => void;
}

export type LaunchRequest = {
  scope: "general" | "scratch" | "project";
  repoRoot: string | null;
  workspaceId: string;
  port: number;
  token: string | null;
};

export class ManagedRuntimes {
  private running = false;
  private generation = 0;
  readonly tokens = new Map<string, string>();

  constructor(private readonly invoke: Invoke, private readonly sink: ManagedRuntimeSink) {}

  /** 作废进行中的监督读取。 */
  invalidate = () => { this.generation += 1; };
  /** 当前代次：调用方据此判断「这期间有没有切换过」。 */
  currentGeneration = () => this.generation;
  tokenFor = (runtimeId: string) => this.tokens.get(runtimeId) ?? "";

  /** 冷启动读一次；由调用方在其余启动读取也成功后再 adopt，保持「全成功才写界面」。 */
  readInitial = () => Promise.all([
    this.invoke<GatewayProcessStatus>("gateway_process_status", { runtimeId: null }),
    this.invoke<GatewayRecoveryRecord | null>("get_gateway_recovery"),
    this.invoke<GatewayRecoveryRecord[]>("list_gateway_recoveries"),
  ]);

  adoptInitial = ([status, record, recoveries]: Awaited<ReturnType<ManagedRuntimes["readInitial"]>>) => {
    this.running = status.running;
    this.sink.setProcessStatus(status);
    this.sink.setRecoveryRecord(record);
    this.sink.setRuntimeRecoveries(recoveries);
  };

  /** 拉起（或复用）一个托管 runtime，并刷新进程列表与恢复记录。显式给了令牌才覆盖已记的令牌。 */
  launch = async (request: LaunchRequest): Promise<GatewayProcessStatus> => {
    const status = await this.invoke<GatewayProcessStatus>("start_gateway", request);
    if (status.runtimeId && (request.token || !this.tokens.has(status.runtimeId))) {
      this.tokens.set(status.runtimeId, request.token || "");
    }
    this.running = status.running;
    this.generation += 1;
    this.sink.setProcessStatus(status);
    this.sink.setRuntimeProcesses(await this.invoke<GatewayProcessStatus[]>("list_gateway_processes").catch(() => [status]));
    this.sink.setRecoveryRecord(await this.invoke<GatewayRecoveryRecord | null>("get_gateway_recovery", { runtimeId: status.runtimeId ?? null }).catch(() => null));
    this.sink.setRuntimeRecoveries(await this.invoke<GatewayRecoveryRecord[]>("list_gateway_recoveries").catch(() => []));
    return status;
  };

  /** 启动失败后重读当前进程与恢复记录，让设置页显示真实状态。 */
  reloadAfterFailure = async (runtimeId: string | undefined) => {
    this.sink.setProcessStatus(await this.invoke<GatewayProcessStatus>("gateway_process_status", { runtimeId: runtimeId ?? null }).catch(() => EMPTY_PROCESS));
    this.sink.setRecoveryRecord(await this.invoke<GatewayRecoveryRecord | null>("get_gateway_recovery").catch(() => null));
  };

  /** 连的是远端工作区或常驻助理：不是 Desktop 拉起的进程，进程栏显示占位，监督不当它意外退出。 */
  markRemote = (profile: DesktopProjectProfile, assistant: boolean) => {
    this.running = false;
    this.sink.setProcessStatus(remoteProcessStatus(profile, assistant));
    this.sink.setRecoveryRecord(null);
  };

  /** 重新附着到上次仍在跑、但不归本次 Desktop 托管的 runtime。 */
  markAttached = (status: GatewayProcessStatus) => {
    this.running = false;
    this.sink.setProcessStatus(status);
  };

  /** 用户点了停止：停掉当前 runtime，刷新列表，清掉恢复记录。 */
  stopCurrent = async (runtimeId: string | undefined) => {
    this.running = false;
    this.sink.setProcessStatus(await this.invoke<GatewayProcessStatus>("stop_gateway", { runtimeId: runtimeId ?? null }));
    if (runtimeId) this.tokens.delete(runtimeId);
    this.sink.setRuntimeProcesses(await this.invoke<GatewayProcessStatus[]>("list_gateway_processes").catch(() => []));
    this.sink.setRuntimeRecoveries(await this.invoke<GatewayRecoveryRecord[]>("list_gateway_recoveries").catch(() => []));
    this.sink.setRecoveryRecord(null);
  };

  /** 换模型配置要重启：必须真停下来才继续，否则新配置不会生效。 */
  stopForRestart = async (runtimeId: string) => {
    this.running = false;
    const stopped = await this.invoke<GatewayProcessStatus>("stop_gateway", { runtimeId });
    if (stopped.running) throw new Error("当前 runtime 未能停止，模型配置尚未生效");
    this.tokens.delete(runtimeId);
    this.sink.setProcessStatus(stopped);
    this.sink.setRuntimeProcesses(await this.invoke<GatewayProcessStatus[]>("list_gateway_processes").catch(() => []));
  };

  /** 停掉一个不是当前连着的后台 runtime（移除最近项目时）。 */
  stopBackground = async (runtimeId: string) => {
    await this.invoke<GatewayProcessStatus>("stop_gateway", { runtimeId });
    this.tokens.delete(runtimeId);
    this.sink.setRuntimeProcesses(await this.invoke<GatewayProcessStatus[]>("list_gateway_processes").catch(() => []));
  };

  dismissRecovery = async (runtimeId: string | undefined) => {
    await this.invoke("clear_gateway_recovery", { runtimeId: runtimeId ?? null });
    this.sink.setRecoveryRecord(null);
  };

  /**
   * 监督一轮。代次变了（这期间有启动/停止/切换）或已卸载就丢弃结果。当前 runtime 意外退出时先回调
   * （调用方决定要不要断开），再读它的恢复记录并返回提示文案。IPC 失败返回 false，交给轮询退避。
   */
  supervise = async (runtimeId: string | undefined, disposed: () => boolean,
    onUnexpectedStop: (status: GatewayProcessStatus) => Promise<void>): Promise<string | null | false> => {
    const generation = this.generation;
    try {
      const statuses = await this.invoke<GatewayProcessStatus[]>("list_gateway_processes");
      const recoveries = await this.invoke<GatewayRecoveryRecord[]>("list_gateway_recoveries").catch(() => []);
      if (disposed() || generation !== this.generation) return null;
      pruneRuntimeTokens(this.tokens, statuses);
      this.sink.setRuntimeProcesses(statuses);
      this.sink.setRuntimeRecoveries(recoveries);
      if (!runtimeId || runtimeId.startsWith("remote:")) return null;
      const { status, unexpectedlyStopped } = observeCurrentRuntime(statuses, runtimeId, this.running);
      this.running = status.running;
      this.sink.setProcessStatus(status);
      if (!unexpectedlyStopped) return null;
      await onUnexpectedStop(status);
      this.sink.setRecoveryRecord(await this.invoke<GatewayRecoveryRecord | null>("get_gateway_recovery", { runtimeId }).catch(() => null));
      return status.message || "Desktop 托管的 runtime 已退出";
    } catch {
      // 监督器只观察 Desktop 自己的子进程；瞬时 IPC 失败留到下一轮重试。
      return false;
    }
  };
}
