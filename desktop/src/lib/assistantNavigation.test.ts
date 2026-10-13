import { expect, it, vi } from "vitest";
import type { GatewayClient } from "../gateway";
import type { AssistantDispatch, DesktopProjectProfile, TaskItem } from "../types";
import { openAssistantTask } from "./assistantNavigation";

const item: AssistantDispatch = { id: "d1", project: "demo", title: "fix", goal: "repair", criteria: [],
  status: "done", tainted: false, remote_task_id: "task-1", branch: "vorto/fix", result: "", error: "",
  created_at: "", updated_at: "", submission_target: { repo_root: "/srv/demo", port: 8082 } };
const profile: DesktopProjectProfile = { id: "remote-demo", kind: "remote", name: "demo", repoRoot: "/srv/demo",
  baseUrl: "http://server:8082", lastOpenedAt: 0 };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
}

function setup() {
  const actual: TaskItem = { id: "task-1", kind: "dev", status: "done", branch: "vorto/fix", execution_scope: { repo_root: "/srv/demo" } };
  const project = { getTask: vi.fn(async () => actual), matchesRemoteProject: vi.fn((id: string) => id === profile.id) } as unknown as GatewayClient;
  const source = { listResidentProjects: vi.fn(async () => [{ name: "demo", port: 8082, repo_root: "/srv/demo" }]) } as unknown as GatewayClient;
  let client: GatewayClient | null = source;
  const ports = {
    current: () => client === source,
    register: vi.fn(async () => profile),
    switchProject: vi.fn(async () => { client = project; return true; }),
    getProjectClient: () => client,
    onConnected: vi.fn(), onReview: vi.fn(async () => {}), onError: vi.fn(),
  };
  return { source, project, ports, actual, setClient: (next: GatewayClient | null) => { client = next; } };
}

it("resolves the pinned project, focuses the original task and opens the existing review", async () => {
  const test = setup();
  await openAssistantTask(test.source, item, "host", test.ports);
  expect(test.ports.register).toHaveBeenCalledWith("host", { name: "demo", port: 8082, repo_root: "/srv/demo" });
  expect(test.ports.onConnected).toHaveBeenCalledWith(profile, "task-1");
  expect(test.project.getTask).toHaveBeenCalledWith("task-1");
  expect(test.ports.onReview).toHaveBeenCalledWith(test.actual);
});

it("does not redirect a historical task to a changed registry target", async () => {
  const test = setup();
  vi.mocked(test.source.listResidentProjects).mockResolvedValue([{ name: "demo", port: 8083, repo_root: "/srv/demo" }]);
  await expect(openAssistantTask(test.source, item, "host", test.ports)).rejects.toThrow("登记已变化");
  expect(test.ports.register).not.toHaveBeenCalled();
  await expect(openAssistantTask(test.source, { ...item, submission_target: undefined }, "host", test.ports)).rejects.toThrow("缺少固定项目定位");
});

it("late registry and registration responses cannot switch a different active host", async () => {
  const test = setup(); const late = deferred<Awaited<ReturnType<GatewayClient["listResidentProjects"]>>>();
  vi.mocked(test.source.listResidentProjects).mockReturnValue(late.promise);
  const loading = openAssistantTask(test.source, item, "host", test.ports);
  test.setClient(null); late.resolve([{ name: "demo", port: 8082, repo_root: "/srv/demo" }]); await loading;
  expect(test.ports.register).not.toHaveBeenCalled();
  const next = setup(); const registration = deferred<DesktopProjectProfile>();
  next.ports.register.mockReturnValue(registration.promise);
  const switching = openAssistantTask(next.source, item, "host", next.ports);
  await Promise.resolve(); next.setClient(null); registration.resolve(profile); await switching;
  expect(next.ports.switchProject).not.toHaveBeenCalled();
});

it("cancelled switching and mismatched connections do not fetch or focus tasks", async () => {
  const test = setup(); test.ports.switchProject.mockResolvedValue(false);
  await openAssistantTask(test.source, item, "host", test.ports);
  expect(test.project.getTask).not.toHaveBeenCalled(); expect(test.ports.onConnected).not.toHaveBeenCalled();
  const next = setup(); vi.mocked(next.project.matchesRemoteProject).mockReturnValue(false);
  await openAssistantTask(next.source, item, "host", next.ports);
  expect(next.project.getTask).not.toHaveBeenCalled();
});

it("a task response from an old project cannot open review in the new project", async () => {
  const test = setup(); const late = deferred<TaskItem>();
  vi.mocked(test.project.getTask).mockReturnValue(late.promise);
  const opening = openAssistantTask(test.source, item, "host", test.ports);
  await vi.waitFor(() => expect(test.project.getTask).toHaveBeenCalled());
  test.setClient(null); late.resolve(test.actual); await opening;
  expect(test.ports.onReview).not.toHaveBeenCalled(); expect(test.ports.onError).not.toHaveBeenCalled();
});

it("wrong task identity or runtime directory cannot open review and reports a current error", async () => {
  for (const actual of [{ id: "wrong" }, { execution_scope: { repo_root: "/srv/other" } }]) {
    const test = setup(); vi.mocked(test.project.getTask).mockResolvedValue({ ...test.actual, ...actual });
    await expect(openAssistantTask(test.source, item, "host", test.ports)).rejects.toThrow("不匹配");
    expect(test.ports.onReview).not.toHaveBeenCalled(); expect(test.ports.onError).toHaveBeenCalledTimes(1);
  }
});

it("canonical runtime roots are pinned independently of the registry alias", async () => {
  const test = setup();
  vi.mocked(test.project.getTask).mockResolvedValue({ ...test.actual, execution_scope: { repo_root: "/real/demo" } });
  await openAssistantTask(test.source, { ...item, submission_runtime_root: "/real/demo" }, "host", test.ports);
  expect(test.ports.onReview).toHaveBeenCalledTimes(1);
});
