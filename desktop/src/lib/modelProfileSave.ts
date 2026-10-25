import { restartSavedSetting, type SettingsFeedback, type SettingsFeedbackKind } from "./settingsApply";
export type ModelSaveKind = SettingsFeedbackKind;
export type ModelSaveFeedback = SettingsFeedback;

/** Persistence has already succeeded. A restart failure must not turn it into a failed save. */
export async function restartSavedModelProfile(
  restart: () => Promise<boolean>, action: "保存" | "清除",
): Promise<ModelSaveFeedback> {
  return restartSavedSetting(restart, `模型配置已${action}`);
}
