import type { AssistantConnector } from "../types";
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
