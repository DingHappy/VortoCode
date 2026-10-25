export type SettingsFeedbackKind = "idle" | "saved" | "pending" | "error";
export type SettingsFeedback = { kind: SettingsFeedbackKind; message: string };

/** The write already succeeded. Restart errors must never be reported as failed writes. */
export async function restartSavedSetting(restart: () => Promise<boolean>, done: string): Promise<SettingsFeedback> {
  try {
    return await restart()
      ? { kind: "saved", message: `${done}，本机引擎已重启` }
      : { kind: "pending", message: `${done}，下次启动本机引擎时生效` };
  } catch {
    return { kind: "pending", message: `${done}；重启失败，请稍后重新启动本机引擎` };
  }
}
