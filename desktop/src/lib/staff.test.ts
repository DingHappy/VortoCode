import { afterEach, describe, expect, it, vi } from "vitest";
import { staffController, staffReadCurrent, type StaffClient, type StaffSnapshot, type StaffState } from "./staff";
import { SOURCE_FRESH_MS } from './readFreshness';
const controllers: ReturnType<typeof staffController>[] = [];
afterEach(() => { controllers.splice(0).forEach(controller => controller.dispose()); vi.useRealTimers(); });
function fixture() {
  vi.useFakeTimers(); vi.setSystemTime(1_000);
  const snapshot: StaffSnapshot = { roles: [], members: [{ id: "reader", name: "阅读专员", role: "read", engine: "vortocode", model: "", revision: 1, status: "active", busy: 0, recent_tasks: [] }],
    proposals: [{ id: "proposal", digest: "digest", nonce: "nonce", round: 1, instance: "instance", status: "pending", approval_level: "desktop", reason: "资料调查", snapshot: { kind: "role", data: { id: "research" }, previous: null } }] };
  const client: StaffClient = { readStaff: vi.fn(async () => structuredClone(snapshot)), decideStaff: vi.fn(async () => ({})), setStaffStatus: vi.fn(async () => ({})) };
  const states: StaffState[] = [];
  const controller = staffController(client, state => states.push(state));
  controllers.push(controller);
  return { snapshot, client, states, controller };
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

it('cancels the exact interrupted bundle through the same owner confirmation binding', async () => {
  const f = fixture(); const proposal = f.snapshot.proposals[0];
  proposal.snapshot = { kind: 'bundle', data: {}, previous: null, entries: [] }; proposal.status = 'applying';
  await f.controller.refresh(); await f.controller.decide(proposal, false);
  expect(f.client.decideStaff).toHaveBeenCalledWith('proposal', false, { digest: 'digest', nonce: 'nonce', round: 1, instance: 'instance' });
});

it('rejects completion when the displayed bundle has begun cancelling', async () => {
  const f = fixture(); const proposal = f.snapshot.proposals[0];
  proposal.snapshot = { kind: 'bundle', data: {}, previous: null, entries: [] }; proposal.status = 'cancelling';
  await f.controller.refresh(); await f.controller.decide(proposal, true);
  expect(f.client.decideStaff).not.toHaveBeenCalled(); expect(f.states[f.states.length - 1]?.error).toContain('变化');
});

it('publishes expiry after the shared read window without polling or writing', async () => {
  const f = fixture(); await f.controller.refresh();
  const published = f.states.length;
  vi.advanceTimersByTime(SOURCE_FRESH_MS + 1);
  expect(f.states.length).toBeGreaterThan(published);
  expect(f.client.readStaff).toHaveBeenCalledTimes(1);
  expect(f.client.decideStaff).not.toHaveBeenCalled();
  expect(f.states[f.states.length - 1]?.value).toEqual(f.snapshot);
  expect(staffReadCurrent(f.states[f.states.length - 1])).toBe(false);
});

it('requires an explicit refresh before owner writes against an expired displayed registry', async () => {
  const f = fixture(); await f.controller.refresh();
  vi.advanceTimersByTime(SOURCE_FRESH_MS + 1);
  await f.controller.status(f.snapshot.members[0], 'paused');
  expect(f.client.setStaffStatus).not.toHaveBeenCalled();
  expect(f.client.readStaff).toHaveBeenCalledTimes(1);
  await f.controller.refresh();
  await f.controller.status(f.snapshot.members[0], 'paused');
  expect(f.client.setStaffStatus).toHaveBeenCalledTimes(1);
});

it('does not write when the preflight registry read itself outlives the shared freshness window', async () => {
  const f = fixture(); await f.controller.refresh();
  let finish!: (value: StaffSnapshot) => void;
  vi.mocked(f.client.readStaff).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const pending = f.controller.status(f.snapshot.members[0], 'paused');
  vi.advanceTimersByTime(SOURCE_FRESH_MS + 1);
  finish(f.snapshot); await pending;
  expect(f.client.setStaffStatus).not.toHaveBeenCalled();
  expect(f.states[f.states.length - 1]?.error).toContain('刷新');
});

it('never publishes an expiry or a late read after the registry connection was disposed', async () => {
  const f = fixture(); await f.controller.refresh();
  const published = f.states.length;
  f.controller.dispose();
  expect(vi.getTimerCount()).toBe(0);
  vi.advanceTimersByTime(SOURCE_FRESH_MS + 1);
  f.controller.recheck();
  expect(f.states).toHaveLength(published);
});

it('dates a slow initial read from request start instead of renewing it on arrival', async () => {
  const f = fixture();
  let finish!: (value: StaffSnapshot) => void;
  vi.mocked(f.client.readStaff).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const pending = f.controller.refresh();
  vi.advanceTimersByTime(SOURCE_FRESH_MS + 1);
  finish(f.snapshot); await pending;
  const state = f.states[f.states.length - 1];
  expect(state.checkedAt).toBe(1_000);
  expect(state.value).toEqual(f.snapshot);
  expect(staffReadCurrent(state)).toBe(false);
  expect(f.client.readStaff).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it('retains the old read time on failure and only a successful refresh makes it current again', async () => {
  const f = fixture(); await f.controller.refresh();
  vi.advanceTimersByTime(SOURCE_FRESH_MS + 1);
  vi.mocked(f.client.readStaff).mockRejectedValueOnce(new Error('offline'));
  await f.controller.refresh();
  let state = f.states[f.states.length - 1];
  expect(state.checkedAt).toBe(1_000);
  expect(state.value).toEqual(f.snapshot);
  expect(staffReadCurrent(state)).toBe(false);
  f.controller.recheck();
  expect(f.client.readStaff).toHaveBeenCalledTimes(2);
  await f.controller.refresh();
  state = f.states[f.states.length - 1];
  expect(state.checkedAt).toBe(Date.now());
  expect(staffReadCurrent(state)).toBe(true);
});

it('rechecks a clock rollback locally without reading or granting actions', async () => {
  const f = fixture(); await f.controller.refresh();
  vi.setSystemTime(999); f.controller.recheck();
  expect(staffReadCurrent(f.states[f.states.length - 1])).toBe(false);
  await f.controller.decide(f.snapshot.proposals[0], true);
  expect(f.client.readStaff).toHaveBeenCalledTimes(1);
  expect(f.client.decideStaff).not.toHaveBeenCalled();
});
