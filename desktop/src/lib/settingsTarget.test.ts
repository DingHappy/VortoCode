import { expect, it, vi } from "vitest";
import { settingsTargetLease, settingsTargetSummary } from "./settingsTarget";
import { desktopContext } from "./desktopContext";

function setup() {
  const client = { read: vi.fn(async () => "A"), write: vi.fn(async (_value: string) => "saved") };
  const context = desktopContext({ registrationId: "a", assistantId: "a", profiles: [{ id: "a", name: "同名助手", kind: "remote",
    repoRoot: "@assistant", baseUrl: "https://assistant.example", lastOpenedAt: 0 }], baseUrl: "https://assistant.example",
    scope: "general", repoRoot: "", sid: "chat", runtimeId: "", connection: "connected", localResidentId: null,
    source: { registrationId: "a", baseUrl: "https://assistant.example", sid: "chat", epoch: 1, generation: 1 } });
  let current = { context, client };
  const lease = settingsTargetLease(() => current);
  return { client, context, lease, change: (value: typeof current) => { current = value; } };
}

it("binds writes to the displayed client and rejects old callbacks after a same-name target switch", async () => {
  const { client, context, lease, change } = setup();
  await lease.client!.write("reviewed"); expect(client.write).toHaveBeenCalledExactlyOnceWith("reviewed");
  const next = { read: vi.fn(async () => "B"), write: vi.fn(async (_value: string) => "saved") };
  change({ context: { ...context, readKey: "B" }, client: next });
  await expect(lease.client!.write("stale")).rejects.toThrow("目标已变化");
  expect(next.write).not.toHaveBeenCalled(); expect(client.write).toHaveBeenCalledOnce();
});

it("invalidates late reads, a replaced client, and A→B→A generations without retrying writes", async () => {
  const { client, context, lease, change } = setup();
  let finish!: (value: string) => void;
  client.read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const pending = lease.client!.read();
  change({ context: { ...context, readKey: "reconnected-A" }, client }); finish("old A");
  await expect(pending).rejects.toThrow("目标已变化");
  change({ context, client: { ...client } });
  await expect(lease.run(async () => "value")).rejects.toThrow("目标已变化");
  expect(client.read).toHaveBeenCalledOnce();
});

it("keeps unconnected settings unavailable and distinguishes temporary, space and unassigned project targets", async () => {
  const { client, context, change } = setup();
  change({ context: { ...context, ready: false }, client });
  const lease = settingsTargetLease(() => ({ context: { ...context, ready: false }, client }));
  await expect(lease.client!.write("no connection")).rejects.toThrow("尚未连接");
  expect(client.write).not.toHaveBeenCalled();
  expect(settingsTargetSummary(context)).toContain("同名助手");
  expect(settingsTargetSummary({ ...context, space: null, projectRoot: "/project" })).toContain("空间归属待核对");
});

it("does not replay a write whose receipt arrives after the owner changes", async () => {
  const { client, context, lease, change } = setup();
  let finish!: (value: string) => void;
  client.write.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const pending = lease.client!.write("reviewed A");
  change({ context: { ...context, readKey: "B" }, client }); finish("saved on A");
  await expect(pending).rejects.toThrow("未重试");
  expect(client.write).toHaveBeenCalledExactlyOnceWith("reviewed A");
});

it("detects a transport epoch change even before the next UI context render", async () => {
  const { client, context } = setup();
  const transport = { ...client, connectionEpoch: 1 };
  const lease = settingsTargetLease(() => ({ context, client: transport }));
  transport.connectionEpoch++;
  await expect(lease.client!.write("stale")).rejects.toThrow("目标已变化");
  expect(client.write).not.toHaveBeenCalled();
});

it("preserves approval identity and client method ownership without manufacturing proofs", async () => {
  const { context } = setup();
  const confirmation = { digest: "digest", nonce: "nonce", round: 2, instance: "instance" };
  const sent = vi.fn();
  class Client {
    private id = "original";
    async decide(id: string, approve: boolean, proof: typeof confirmation) { sent(this.id, id, approve, proof); }
  }
  const client = new Client(), lease = settingsTargetLease(() => ({ context, client }));
  await lease.client!.decide("proposal", true, confirmation);
  expect(sent).toHaveBeenCalledExactlyOnceWith("original", "proposal", true, confirmation);
});

it("rejects a client replacement before the UI context has caught up", async () => {
  const { client, context } = setup();
  const transport = { ...client, desktopSource: { generation: context.clientGeneration! + 1,
    epoch: context.clientEpoch, sid: context.sid, registrationId: "b" } };
  const lease = settingsTargetLease(() => ({ context, client: transport }));
  expect(lease.current()).toBe(false);
  await expect(lease.client!.write("old displayed target")).rejects.toThrow("目标已变化");
  expect(client.write).not.toHaveBeenCalled();
});
