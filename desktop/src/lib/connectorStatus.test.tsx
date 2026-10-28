import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { CustomizePage } from "../components/CustomizePage";
import type { AssistantConnector } from "../types";
import { connectorCredentialView, connectorDisconnectMessage, ConnectorStatusReads } from "./connectorStatus";

const detail = (connector: AssistantConnector) => renderToStaticMarkup(<CustomizePage plugins={[]} checks={{}} busy=""
  onCheck={() => {}} onRemove={() => {}} onSave={async () => true} connectors={[connector]} onConnector={() => {}}
  skills={null} connected initialTab="connections" initialSelected={`connector:${connector.name}`} />);

const saved: AssistantConnector = {
  name: "fixture", provider: "github", read_only: true, connected: true, expired: false,
  credential_status: "available", credential_available: true, connection_status: "unobserved",
  authorizing: null, error: "",
};

describe("connector credential evidence", () => {
  it("offers actual local disconnection only when the runtime advertises it", () => {
    const html = detail({ ...saved, local_revocation_supported: true });
    expect(html).toContain("断开本机连接");
    const legacy = detail(saved);
    expect(legacy).toContain("移除凭据");
  });
  it("renders valid credentials without claiming a service connection", () => {
    const html = detail(saved);
    expect(html).toContain("凭据可用");
    expect(html).not.toContain("已连接");
    expect(html).toContain("移除凭据");
    expect(html).not.toContain("不能改任何东西");
  });

  it.each(["missing", "invalid", "expired", "unknown"] as const)("keeps %s credentials unavailable", (status) => {
    const view = connectorCredentialView({ ...saved, credential_status: status, credential_available: false });
    expect(view.available).toBe(false);
    expect(view.label).not.toContain("凭据可用");
    expect(view.label).not.toContain("已连接");
  });

  it("does not infer readiness from an older runtime's connected flag", () => {
    const legacy = { ...saved, credential_status: undefined, credential_available: undefined };
    const view = connectorCredentialView(legacy);
    expect(view.available).toBe(false);
    expect(view.label).toContain("待确认");
    expect(view.canClear).toBe(true);
  });

  it("fails closed on contradictory readiness fields", () => {
    expect(connectorCredentialView({ ...saved, credential_available: false }).available).toBe(false);
    expect(connectorCredentialView({ ...saved, credential_status: "expired" }).available).toBe(false);
  });

  it("allows cancelling a pending authorization without claiming usable credentials", () => {
    const view = connectorCredentialView({ ...saved, connected: false, credential_status: "missing", credential_available: false,
      authorizing: { user_code: "DEMO-0000", verification_uri: "https://github.com/login/device", expires_at: 1000 } });
    expect(view.label).toContain("等待");
    expect(view.canClear).toBe(true);
    expect(view.available).toBe(false);
  });

  it("does not label a configured write connector as read-only", () => {
    expect(connectorCredentialView({ ...saved, read_only: false }).label).not.toContain("只读");
  });
});

describe("local disconnect receipts", () => {
  it("distinguishes confirmed local cleanup from remote cancellation", () => {
    const text = connectorDisconnectMessage({ removed: true, calls_blocked: true, revocation_scope: "current_process",
      credential_cleanup_confirmed: true, cleanup_status: "confirmed", remote_requests_verified: false });
    expect(text).toContain("已禁用"); expect(text).toContain("清理已确认");
    expect(text).toContain("仍需单独核对"); expect(text).not.toContain("远端已停止");
  });
  it("keeps failed file and transport cleanup visible", () => {
    const text = connectorDisconnectMessage({ removed: false, calls_blocked: true, revocation_scope: "current_process",
      credential_cleanup_confirmed: false, cleanup_status: "unconfirmed" });
    expect(text).toContain("文件清理未确认"); expect(text).toContain("连接清理未确认");
    expect(text).toContain("已禁用");
  });
  it("never upgrades a legacy delete response to a revocation proof", () => {
    const text = connectorDisconnectMessage({ removed: true });
    expect(text).toContain("尚未确认"); expect(text).not.toContain("已禁用");
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}

describe("connector status ownership", () => {
  it("ignores a previous host's delayed response", async () => {
    const data = deferred<AssistantConnector[]>();
    const client = { listConnectors: vi.fn(() => data.promise) };
    const publish = vi.fn();
    let current = true;
    const reading = new ConnectorStatusReads().read(client, "host-a", () => current, publish);
    current = false; data.resolve([saved]); await reading;
    expect(publish).not.toHaveBeenCalled();
  });

  it("coalesces slow authorization polls instead of starving updates", async () => {
    const data = deferred<AssistantConnector[]>();
    const client = { listConnectors: vi.fn(() => data.promise) };
    const reads = new ConnectorStatusReads(); const publish = vi.fn();
    const first = reads.read(client, "host-a", () => true, publish);
    expect(reads.read(client, "host-a", () => true, publish)).toBe(first);
    expect(client.listConnectors).toHaveBeenCalledTimes(1);
    data.resolve([saved]); await first;
    expect(publish).toHaveBeenCalledWith([saved]);
  });

  it("cannot publish pre-write state or clear the newer pending read", async () => {
    const old = deferred<AssistantConnector[]>(); const fresh = deferred<AssistantConnector[]>();
    const client = { listConnectors: vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise) };
    const reads = new ConnectorStatusReads(); const publish = vi.fn();
    const first = reads.read(client, "host-a", () => true, publish);
    reads.invalidate();
    const second = reads.read(client, "host-a", () => true, publish);
    old.resolve([saved]); await first;
    expect(publish).not.toHaveBeenCalled();
    expect(reads.read(client, "host-a", () => true, publish)).toBe(second);
    fresh.resolve([]); await second;
    expect(publish).toHaveBeenCalledExactlyOnceWith([]);
  });

  it("does not query a stale host", async () => {
    const client = { listConnectors: vi.fn() };
    await new ConnectorStatusReads().read(client, "host-a", () => false, vi.fn());
    expect(client.listConnectors).not.toHaveBeenCalled();
  });
});
