import type { DesktopProjectProfile } from "../types";
import type { LocalResidentStatus } from "./localResident";
import { errorText } from "./errorText";

type SelectionOptions = {
  prepare?: (profile: DesktopProjectProfile) => Promise<true | string>;
  switchWorkspace: (body: () => Promise<boolean>) => Promise<boolean>;
  persist: (profile: DesktopProjectProfile) => Promise<void>;
  connection?: () => unknown;
  isSelected?: (profile: DesktopProjectProfile) => boolean;
  onError?: (message: string) => void;
};

/** Only native code changes the persisted default; local hosts keep their launch-identity gate. */
export async function persistResidentSelection(profile: DesktopProjectProfile, local: LocalResidentStatus | null,
  invoke: (command: string, args: Record<string, unknown>) => Promise<unknown>): Promise<void> {
  if (local?.projectId === profile.id) {
    if (local.state !== "running" || !local.revision || !local.launchId) throw new Error("本机常驻运行实例尚未核对，请刷新后重试");
    await invoke("local_resident_select", { expectedRevision: local.revision, expectedLaunchId: local.launchId });
  } else {
    await invoke("select_registered_assistant", { projectId: profile.id });
  }
}

/** Attachment, persistence and activation belong to the same workspace switch. */
export async function connectResidentSelection(profile: DesktopProjectProfile,
  connect: (profile: DesktopProjectProfile) => Promise<true | string>,
  activate: (profile: DesktopProjectProfile) => void, options: SelectionOptions): Promise<void> {
  try {
    const prepared = await options.prepare?.(profile);
    if (prepared !== undefined && prepared !== true) throw new Error(prepared || "连接常驻助手失败");
    let failure: unknown;
    const select = async () => {
      try {
        const result = await connect(profile);
        if (result !== true) throw new Error(result || "连接常驻助手失败");
        const attached = options.connection?.();
        const current = () => (!options.isSelected || options.isSelected(profile))
          && (!options.connection || Boolean(attached) && options.connection() === attached);
        if (!current()) throw new Error("连接位置已变化，未切换当前对话");
        await options.persist(profile);
        if (!current()) throw new Error("连接位置已变化，未切换当前对话");
        activate(profile);
        return true;
      } catch (error) { failure = error; throw error; }
    };
    if (!await options.switchWorkspace(select)) throw failure ?? new Error("工作区正在切换，请稍后重试");
  } catch (error) {
    options.onError?.(errorText(error, "连接常驻助手失败"));
    throw error;
  }
}
