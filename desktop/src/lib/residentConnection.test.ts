import { expect, it, vi } from "vitest";
import { connectResidentSelection, persistResidentSelection } from "./residentConnection";
import type { DesktopProjectProfile } from "../types";
import { ConnectionCoordinator } from "../connection/connectionCoordinator";
import { localResidentController, type LocalResidentClient, type LocalResidentStatus } from "./localResident";

const profile: DesktopProjectProfile = { id: "fixture-host", name: "测试常驻助手", kind: "remote",
  repoRoot: "@assistant", baseUrl: "http://127.0.0.1:18765", lastOpenedAt: 0 };
const selectionOptions = () => ({ switchWorkspace: (body: () => Promise<boolean>) => new ConnectionCoordinator().exclusiveSwitch(body),
  persist: vi.fn(async () => {}) });

it("does not activate a host or open its chat when the connection returns an error string", async () => {
  const activate = vi.fn();
  const options = selectionOptions();
  await expect(connectResidentSelection(profile, async () => "无法连接测试服务", activate, options)).rejects.toThrow("无法连接测试服务");
  expect(activate).not.toHaveBeenCalled();
  expect(options.persist).not.toHaveBeenCalled();
});

it("activates exactly the explicitly selected host after confirmed connection", async () => {
  const activate = vi.fn();
  const connect = vi.fn(async () => true as const);
  await connectResidentSelection(profile, connect, activate, selectionOptions());
  expect(connect).toHaveBeenCalledExactlyOnceWith(profile);
  expect(activate).toHaveBeenCalledExactlyOnceWith(profile);
});

it("keeps the original selection when connection throws", async () => {
  const activate = vi.fn();
  await expect(connectResidentSelection(profile, async () => { throw new Error("probe unavailable"); }, activate, selectionOptions()))
    .rejects.toThrow("probe unavailable");
  expect(activate).not.toHaveBeenCalled();
});

it("holds the workspace switch through delayed persistence and activates without another attachment", async () => {
  const coordinator = new ConnectionCoordinator();
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  const persist = vi.fn(() => pending);
  const connect = vi.fn(async () => true as const);
  const activate = vi.fn();
  const selection = connectResidentSelection(profile, connect, activate, {
    switchWorkspace: body => coordinator.exclusiveSwitch(body), persist,
  });
  await vi.waitFor(() => expect(persist).toHaveBeenCalledOnce());
  expect(coordinator.switching).toBe(true);
  const newerSwitch = vi.fn(async () => true);
  expect(await coordinator.exclusiveSwitch(newerSwitch)).toBe(false);
  expect(newerSwitch).not.toHaveBeenCalled();
  expect(activate).not.toHaveBeenCalled();
  finish(); await selection;
  expect(activate).toHaveBeenCalledExactlyOnceWith(profile);
  expect(connect).toHaveBeenCalledOnce();
  expect(coordinator.switching).toBe(false);
});

it("reports persistence failure through the application owner after the settings surface has gone", async () => {
  const coordinator = new ConnectionCoordinator();
  const activate = vi.fn(), onError = vi.fn();
  await expect(connectResidentSelection(profile, async () => true, activate, {
    switchWorkspace: body => coordinator.exclusiveSwitch(async () => {
      try { return await body(); } catch { return false; }
    }),
    persist: async () => { throw new Error("连接期间常驻实例已变化"); }, onError,
  })).rejects.toThrow("连接期间常驻实例已变化");
  expect(onError).toHaveBeenCalledWith("连接期间常驻实例已变化");
  expect(activate).not.toHaveBeenCalled();
  expect(coordinator.switching).toBe(false);
});

it("keeps native selection failures in the application banner after the card controller is disposed", async () => {
  const value: LocalResidentStatus = { supported: true, installed: true, state: "running", availableVersion: "fixture",
    cronEnabled: false, projectId: profile.id, revision: "r1", launchId: "l1", message: "" };
  const read = vi.fn(async () => value);
  const client: LocalResidentClient = { read, install: read, start: read, stop: read, update: read, applyModel: read,
    connect: vi.fn(async () => profile) };
  const coordinator = new ConnectionCoordinator(), onError = vi.fn(), activate = vi.fn();
  const cardStates: string[] = [];
  const controller = localResidentController(client, state => cardStates.push(state.error), selected =>
    connectResidentSelection(selected, async () => {
      controller.dispose(); // The successful attachment closes SettingsModal.
      return true;
    }, activate, { switchWorkspace: body => coordinator.exclusiveSwitch(body),
      persist: async () => { throw "常驻实例已变化，未切换默认助手"; }, onError }));
  await controller.refresh(); await controller.change("connect");
  expect(onError).toHaveBeenCalledExactlyOnceWith("常驻实例已变化，未切换默认助手");
  expect(activate).not.toHaveBeenCalled();
  expect(cardStates.every(error => !error)).toBe(true);
});

it("discards late activation if a newer session attachment replaces the selected connection", async () => {
  const coordinator = new ConnectionCoordinator();
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  let client = {}, host = profile.id;
  const activate = vi.fn(), persist = vi.fn(() => pending);
  const selection = connectResidentSelection(profile, async () => true, activate, {
    switchWorkspace: body => coordinator.exclusiveSwitch(body), persist,
    connection: () => client, isSelected: selected => host === selected.id,
  });
  const settled = expect(selection).rejects.toThrow("连接位置已变化");
  await vi.waitFor(() => expect(persist).toHaveBeenCalled());
  client = {}; host = "newer-host";
  finish(); await settled;
  expect(activate).not.toHaveBeenCalled();
});

it("persists an existing remote choice instead of only reordering the view", async () => {
  const invoke = vi.fn(async () => undefined);
  await persistResidentSelection(profile, null, invoke);
  expect(invoke).toHaveBeenCalledExactlyOnceWith("select_registered_assistant", { projectId: profile.id });
});

it("keeps local persistence on its exact native revision and launch check", async () => {
  const invoke = vi.fn(async () => undefined);
  await persistResidentSelection(profile, { supported: true, installed: true, state: "running", availableVersion: "fixture",
    cronEnabled: false, projectId: profile.id, revision: "rev-one", launchId: "launch-one", message: "" }, invoke);
  expect(invoke).toHaveBeenCalledExactlyOnceWith("local_resident_select", { expectedRevision: "rev-one", expectedLaunchId: "launch-one" });
});

it("never routes an unchecked local launch through the remote preference command", async () => {
  const invoke = vi.fn(async () => undefined);
  await expect(persistResidentSelection(profile, { supported: true, installed: true, state: "running", availableVersion: "fixture",
    cronEnabled: false, projectId: profile.id, revision: "rev-one", message: "" }, invoke)).rejects.toThrow("运行实例尚未核对");
  expect(invoke).not.toHaveBeenCalled();
});
