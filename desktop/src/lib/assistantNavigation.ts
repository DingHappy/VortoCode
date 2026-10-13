import type { GatewayClient } from "../gateway";
import type { AssistantDispatch, DesktopProjectProfile, TaskItem } from "../types";
import type { ResidentProject } from "./residentPlace";

type Ports = {
  current: () => boolean;
  register: (hostId: string, project: ResidentProject) => Promise<DesktopProjectProfile>;
  switchProject: (project: DesktopProjectProfile) => Promise<boolean>;
  getProjectClient: () => GatewayClient | null;
  onConnected: (project: DesktopProjectProfile, taskId: string) => void;
  onReview: (task: TaskItem) => Promise<void>;
  onError: (error: unknown) => void;
};

/** Resolve only the pinned project; every asynchronous boundary keeps its owner. */
export async function openAssistantTask(source: GatewayClient, task: AssistantDispatch, hostId: string, ports: Ports) {
  if (!ports.current()) return;
  if (!/^[A-Za-z0-9._-]{1,180}$/.test(task.remote_task_id)) throw new Error("任务标识无效，请核对任务单");
  if (!task.submission_target) throw new Error("历史任务单缺少固定项目定位，请在工作台手动核对");
  const registry = await source.listResidentProjects();
  if (!ports.current()) return;
  const entry = registry.find(project => project.name.toLowerCase() === task.project.toLowerCase());
  if (!entry) throw new Error("常驻电脑上没有登记对应项目，请核对项目配置");
  if (entry.port !== task.submission_target.port || entry.repo_root !== task.submission_target.repo_root) {
    throw new Error("项目登记已变化，未打开其它项目；请核对原任务所在的项目");
  }
  const profile = await ports.register(hostId, entry);
  if (!ports.current()) return;
  if (!profile.id || profile.kind !== "remote" || profile.repoRoot !== entry.repo_root) {
    throw new Error("登记结果与目标项目不匹配，未切换项目");
  }
  if (!await ports.switchProject(profile)) return;
  const client = ports.getProjectClient();
  if (!client || !client.matchesRemoteProject(profile.id)) return;
  const currentProject = () => ports.getProjectClient() === client && client.matchesRemoteProject(profile.id);
  ports.onConnected(profile, task.remote_task_id);
  try {
    const actual = await client.getTask(task.remote_task_id);
    if (!currentProject()) return;
    if (actual.id !== task.remote_task_id) throw new Error("项目返回的任务标识不匹配，请核对状态");
    if (actual.execution_scope?.repo_root !== (task.submission_runtime_root ?? profile.repoRoot)) {
      throw new Error("项目运行时的实际目录未确认或不匹配，请核对项目服务");
    }
    if (actual.status === "done" && (actual.branch || actual.plan?.branch)) await ports.onReview(actual);
  } catch (error) {
    if (currentProject()) ports.onError(error);
    throw error;
  }
}
