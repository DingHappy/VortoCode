import { describe, expect, it, vi } from "vitest";
import { staffController, type StaffClient, type StaffSnapshot, type StaffState } from "./staff";
function fixture() {
  const snapshot: StaffSnapshot = { roles: [], members: [{ id: "reader", name: "阅读专员", role: "read", engine: "vortocode", model: "", revision: 1, status: "active", busy: 0, recent_tasks: [] }],
    proposals: [{ id: "proposal", digest: "digest", nonce: "nonce", round: 1, instance: "instance", status: "pending", approval_level: "desktop", reason: "资料调查", snapshot: { kind: "role", data: { id: "research" }, previous: null } }] };
  const client: StaffClient = { readStaff: vi.fn(async () => structuredClone(snapshot)), decideStaff: vi.fn(async () => ({})), setStaffStatus: vi.fn(async () => ({})) };
  const states: StaffState[] = [];
  return { snapshot, client, states, controller: staffController(client, state => states.push(state)) };
}
describe("staff owner controls", () => {
  it("only an owner decision submits the exact displayed proposal, without native signing", async () => {
    const f = fixture(); await f.controller.refresh(); expect(f.client.decideStaff).not.toHaveBeenCalled();
    await f.controller.decide(f.snapshot.proposals[0], true);
    expect(f.client.decideStaff).toHaveBeenCalledWith("proposal", true, { digest: "digest", nonce: "nonce", round: 1, instance: "instance" });
    expect(f.states[f.states.length - 1]?.busy).toBe(false);
  });
  it.each(["digest", "nonce", "round", "instance"] as const)("rejects a changed %s without writing", async field => {
    const f = fixture(); await f.controller.refresh(); const shown = structuredClone(f.snapshot.proposals[0]);
    if (field === "round") f.snapshot.proposals[0].round = 2; else f.snapshot.proposals[0][field] = "changed";
    await f.controller.decide(shown, true);
    expect(f.client.decideStaff).not.toHaveBeenCalled(); expect(f.states[f.states.length - 1]?.error).toContain("变化");
  });
  it("disposal during a refresh cannot send a late decision", async () => {
    const f = fixture(); await f.controller.refresh();
    let finish!: (value: StaffSnapshot) => void;
    vi.mocked(f.client.readStaff).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const pending = f.controller.decide(f.snapshot.proposals[0], true);
    f.controller.dispose(); finish(f.snapshot); await pending;
    expect(f.client.decideStaff).not.toHaveBeenCalled();
  });
  it("refuses stale status changes without writing", async () => {
    const f = fixture(); await f.controller.refresh(); const member = structuredClone(f.snapshot.members[0]); f.snapshot.members[0].revision = 2;
    await f.controller.status(member, "paused"); expect(f.client.setStaffStatus).not.toHaveBeenCalled();
  });
  it("uses the displayed member revision and rereads actual status", async () => {
    const f = fixture(); await f.controller.refresh(); vi.mocked(f.client.setStaffStatus).mockImplementation(async () => { f.snapshot.members[0].status = "paused"; f.snapshot.members[0].revision = 2; return {}; });
    await f.controller.status(f.snapshot.members[0], "paused"); expect(f.client.setStaffStatus).toHaveBeenCalledWith("reader", "paused", 1); expect(f.states[f.states.length - 1]?.value?.members[0].status).toBe("paused");
  });
  it("rejects through the same owner route and blocks retries until refresh after a storage error", async () => {
    const f = fixture(); await f.controller.refresh(); vi.mocked(f.client.decideStaff).mockRejectedValueOnce(new Error("存储失败，结果待核对"));
    await f.controller.decide(f.snapshot.proposals[0], false);
    expect(f.client.decideStaff).toHaveBeenCalledWith("proposal", false, { digest: "digest", nonce: "nonce", round: 1, instance: "instance" });
    expect(f.states[f.states.length - 1]?.error).toContain("结果待核对");
    await f.controller.decide(f.snapshot.proposals[0], false); expect(f.client.decideStaff).toHaveBeenCalledTimes(1);
  });
});

it('only creates a pending development proposal without automatically approving', async () => {
  const f = fixture(); f.snapshot.development_projects = [{ name: 'Example' }];
  f.client.proposeDevelopment = vi.fn(async () => ({}));
  await f.controller.refresh();
  await f.controller.proposeDevelopment({ kind: 'role', id: 'example-dev', name: '项目开发', project: 'Example' });
  expect(f.client.proposeDevelopment).toHaveBeenCalledWith({ kind: 'role', id: 'example-dev', name: '项目开发', project: 'Example' });
  expect(f.client.decideStaff).not.toHaveBeenCalled();
});
it('refuses a development target removed after the form was displayed', async () => {
  const f = fixture(); f.snapshot.development_projects = [{ name: 'Example' }];
  f.client.proposeDevelopment = vi.fn(async () => ({}));
  await f.controller.refresh(); f.snapshot.development_projects = [];
  await f.controller.proposeDevelopment({ kind: 'role', id: 'example-dev', name: '项目开发', project: 'Example' });
  expect(f.client.proposeDevelopment).not.toHaveBeenCalled();
});
