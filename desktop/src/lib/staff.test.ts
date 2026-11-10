import { describe, expect, it, vi } from "vitest";
import { staffController, type StaffClient, type StaffSnapshot, type StaffState } from "./staff";
function fixture() {
  const snapshot: StaffSnapshot = { roles: [], members: [{ id: "reader", name: "阅读专员", role: "read", engine: "vortocode", model: "", revision: 1, status: "active", busy: 0, recent_tasks: [] }],
    proposals: [{ id: "proposal", digest: "digest", nonce: "nonce", status: "pending", requires_desktop_key: true, reason: "资料调查", snapshot: { kind: "role", data: { id: "research" }, previous: null } }],
    approval_key: { instance: "instance", key: { id: "key", public_key: "public" } } };
  const client: StaffClient = { readStaff: vi.fn(async () => structuredClone(snapshot)), decideStaff: vi.fn(async () => ({})), bindStaffKey: vi.fn(async () => ({})), setStaffStatus: vi.fn(async () => ({})) };
  const states: StaffState[] = [], sign = vi.fn(async () => ({ public_key: "public", signature: "signed" }));
  return { snapshot, client, states, sign, controller: staffController(client, state => states.push(state), sign) };
}
describe("staff owner controls", () => {
  it("signs the exact pending proposal before high-privilege approval", async () => {
    const f = fixture(); await f.controller.refresh(); await f.controller.decide(f.snapshot.proposals[0], true);
    expect(f.sign).toHaveBeenCalledWith({ instance: "instance", proposalId: "proposal", digest: "digest", nonce: "nonce" });
    expect(f.client.decideStaff).toHaveBeenCalledWith("proposal", true, "digest", "signed"); expect(f.states[f.states.length - 1]?.busy).toBe(false);
  });
  it("enrolls only on an owner action and then signs the proposal separately", async () => {
    const f = fixture(); f.snapshot.approval_key.key = null;
    vi.mocked(f.client.bindStaffKey).mockImplementation(async () => { f.snapshot.approval_key.key = { id: "key", public_key: "public" }; return {}; });
    await f.controller.refresh(); expect(f.sign).not.toHaveBeenCalled();
    await f.controller.decide(f.snapshot.proposals[0], true);
    expect(f.sign).toHaveBeenNthCalledWith(1, { instance: "instance" }); expect(f.sign).toHaveBeenCalledTimes(2);
    expect(f.client.bindStaffKey).toHaveBeenCalledWith({ public_key: "public", signature: "signed" }); expect(f.client.decideStaff).toHaveBeenCalledTimes(1);
  });
  it("never grants when the hardware signer fails or returns another key", async () => {
    const f = fixture(); await f.controller.refresh(); f.sign.mockRejectedValueOnce(new Error("Touch ID 未批准"));
    await f.controller.decide(f.snapshot.proposals[0], true); expect(f.client.decideStaff).not.toHaveBeenCalled(); expect(f.states[f.states.length - 1]?.error).toContain("Touch ID");
    await f.controller.refresh(); f.sign.mockResolvedValueOnce({ public_key: "wrong-key", signature: "signed" });
    await f.controller.decide(f.snapshot.proposals[0], true); expect(f.client.decideStaff).not.toHaveBeenCalled();
  });
  it("discards late signatures after the page is disposed", async () => {
    const f = fixture(); await f.controller.refresh(); let finish!: (value: { public_key: string; signature: string }) => void;
    f.sign.mockReturnValue(new Promise(resolve => { finish = resolve; })); const pending = f.controller.decide(f.snapshot.proposals[0], true);
    await Promise.resolve(); await Promise.resolve(); f.controller.dispose(); finish({ public_key: "public", signature: "signed" }); await pending;
    expect(f.client.decideStaff).not.toHaveBeenCalled();
  });
  it("refuses stale status changes and stale proposals without writing", async () => {
    const f = fixture(); await f.controller.refresh(); const member = structuredClone(f.snapshot.members[0]); f.snapshot.members[0].revision = 2;
    await f.controller.status(member, "paused"); expect(f.client.setStaffStatus).not.toHaveBeenCalled(); await f.controller.refresh();
    const proposal = structuredClone(f.snapshot.proposals[0]); f.snapshot.proposals[0].digest = "updated";
    await f.controller.decide(proposal, true); expect(f.sign).not.toHaveBeenCalled(); expect(f.client.decideStaff).not.toHaveBeenCalled();
  });
  it("uses the displayed member revision and rereads actual status", async () => {
    const f = fixture(); await f.controller.refresh(); vi.mocked(f.client.setStaffStatus).mockImplementation(async () => { f.snapshot.members[0].status = "paused"; f.snapshot.members[0].revision = 2; return {}; });
    await f.controller.status(f.snapshot.members[0], "paused"); expect(f.client.setStaffStatus).toHaveBeenCalledWith("reader", "paused", 1); expect(f.states[f.states.length - 1]?.value?.members[0].status).toBe("paused");
  });
  it("rejects without a key and makes failed outcomes explicit", async () => {
    const f = fixture(); await f.controller.refresh(); vi.mocked(f.client.decideStaff).mockRejectedValueOnce(new Error("存储失败，结果待核对"));
    await f.controller.decide(f.snapshot.proposals[0], false); expect(f.sign).not.toHaveBeenCalled();
    expect(f.client.decideStaff).toHaveBeenCalledWith("proposal", false, "digest", undefined); expect(f.states[f.states.length - 1]?.error).toContain("结果待核对");
    await f.controller.decide(f.snapshot.proposals[0], false); expect(f.client.decideStaff).toHaveBeenCalledTimes(1);
  });
});
