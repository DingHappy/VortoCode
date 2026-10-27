import { restartSavedSetting, type SettingsFeedback, type SettingsFeedbackKind } from "./settingsApply";
export type ModelSaveKind = SettingsFeedbackKind;
export type ModelSaveFeedback = SettingsFeedback;

export function unchangedModelRevision(before?: string, after?: string): boolean {
  return Boolean(before && after && before === after);
}

/** Persistence has already succeeded. A restart failure must not turn it into a failed save. */
export async function restartSavedModelProfile(
  restart: () => Promise<boolean>, action: "保存" | "清除", unchanged = false,
): Promise<ModelSaveFeedback> {
  if (unchanged) return { kind: "saved", message: "模型配置内容未变化，保留当前引擎状态" };
  return restartSavedSetting(restart, `模型配置已${action}`);
}
