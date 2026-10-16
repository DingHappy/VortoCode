import type { AssistantConnector, ConnectorDisconnectReceipt } from "../types";
import type { GatewayClient } from "../gateway";

/** Credential evidence is independent of session-owned MCP connection observations. */
export function connectorCredentialView(item: AssistantConnector) {
  const known = ["missing", "available", "invalid", "expired", "unknown"].includes(item.credential_status || "");
  let status = known ? item.credential_status! : "unknown";
  if ((status === "available") !== (item.credential_available === true)) status = "unknown";
  const available = status === "available";
  const labels = { missing: "未保存凭据", available: "凭据可用", invalid: "凭据不可用", expired: "凭据已过期", unknown: "凭据状态待确认" };
  return {
    available,
    label: !available && item.authorizing ? "等待 GitHub 授权…" : labels[status] + (available && item.read_only ? " · 只读配置" : ""),
    canClear: available || status === "invalid" || status === "expired" || item.credential_status === "unknown" || item.connected === true || !!item.authorizing,
  };
}

export function connectorDisconnectMessage(receipt: ConnectorDisconnectReceipt | null | undefined): string {
  if (receipt?.calls_blocked !== true || receipt.revocation_scope !== "current_process") {
    return "凭据移除请求已返回；旧连接是否禁用尚未确认，请核对或更新运行实例。";
  }
  return ["本运行实例已禁用此连接器的后续调用。",
    receipt.credential_cleanup_confirmed === true ? "本机凭据清理已确认。" : "凭据文件清理未确认，请检查本机权限。",
    receipt.cleanup_status === "confirmed" ? "本地连接清理已确认。" : "本地连接清理未确认，请核对运行实例及凭据清理状态。",
    "已发出的远端请求及 GitHub 授权仍需单独核对。"].join("\n");
}

/** Coalesce reads and invalidate responses that predate a write or host change. */
export class ConnectorStatusReads {
  private epoch = 0;
  private pending?: { client: object; scope: string; epoch: number; promise: Promise<void> };

  invalidate() { this.epoch++; this.pending = undefined; }

  read(client: Pick<GatewayClient, "listConnectors">, scope: string, current: () => boolean, publish: (rows: AssistantConnector[]) => void): Promise<void> {
    if (!current()) return Promise.resolve();
    if (this.pending?.client === client && this.pending.scope === scope) return this.pending.promise;
    const epoch = ++this.epoch;
    const promise = (async () => {
      const rows = await client.listConnectors();
      if (epoch === this.epoch && current()) publish(rows);
    })().finally(() => { if (this.pending?.epoch === epoch) this.pending = undefined; });
    this.pending = { client, scope, epoch, promise };
    return promise;
  }
}
