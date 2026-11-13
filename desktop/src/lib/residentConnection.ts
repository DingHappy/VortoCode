import type { DesktopProjectProfile } from "../types";

/** Shared by local-resident and remote-resident selection; a failed probe leaves selection intact. */
export async function connectResidentSelection(profile: DesktopProjectProfile,
  connect: (profile: DesktopProjectProfile) => Promise<true | string>,
  activate: (profile: DesktopProjectProfile) => Promise<void> | void): Promise<void> {
  const result = await connect(profile);
  if (result !== true) throw new Error(result || "连接常驻助手失败");
  await activate(profile);
}
