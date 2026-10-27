import { expect, it } from "vitest";
import { isModelProfileConflict } from "./modelProfileConflict";
import { errorText } from "./errorText";

it("recognizes the native conflict code without depending on translated wording", () => {
  const error = { code: "model_configuration_conflict", message: "配置有更新" };
  expect(isModelProfileConflict(error)).toBe(true);
  expect(isModelProfileConflict({ ...error, message: "Configuration changed" })).toBe(true);
  expect(errorText(error, "保存失败")).toBe("配置有更新");
});

it("does not offer conflict recovery for cancellation, disk failures or message-only guesses", () => {
  for (const error of [null, "model_configuration_conflict", new Error("模型配置已被其他操作更新"),
    { code: "model_configuration_error", message: "磁盘不可写" }, { message: "模型配置已被其他操作更新" }]) {
    expect(isModelProfileConflict(error)).toBe(false);
  }
});
