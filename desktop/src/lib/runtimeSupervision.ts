// 托管 runtime 的监督：Desktop 每 2 秒（后台 10 秒）列一次自己拉起的子进程。纯函数。
import type { GatewayProcessStatus } from "../types";

export const EMPTY_PROCESS: GatewayProcessStatus = {
  running: false,
  message: "本地引擎尚未启动",
};

/** 已退出的 runtime 的连接令牌就地删掉，不留给同端口新起的进程。 */
export function pruneRuntimeTokens(tokens: Map<string, string>, statuses: GatewayProcessStatus[]): void {
  const live = new Set(statuses.filter((item) => item.running).map((item) => item.runtimeId).filter(Boolean));
  for (const runtimeId of tokens.keys()) {
    if (!live.has(runtimeId)) tokens.delete(runtimeId);
  }
}

/**
 * 当前 runtime 这一轮的状态。列表里找不到就当它已停止；上一轮还在跑、这一轮不在了才算意外退出
 * （用户自己点停止时，调用方事先把「上一轮在跑」清掉）。
 */
export function observeCurrentRuntime(statuses: GatewayProcessStatus[], runtimeId: string, wasRunning: boolean):
  { status: GatewayProcessStatus; unexpectedlyStopped: boolean } {
  const status = statuses.find((item) => item.runtimeId === runtimeId) ?? {
    ...EMPTY_PROCESS,
    runtimeId,
    message: "当前 runtime 已停止",
  };
  return { status, unexpectedlyStopped: wasRunning && !status.running };
}
