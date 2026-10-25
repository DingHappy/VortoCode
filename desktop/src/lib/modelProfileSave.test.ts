import { expect, it, vi } from "vitest";
import { restartSavedModelProfile } from "./modelProfileSave";

it("reports an applied configuration only when restart succeeds", async () => {
  expect(await restartSavedModelProfile(async () => true, "保存")).toEqual({ kind: "saved", message: "模型配置已保存，本机引擎已重启" });
});
it("keeps a saved configuration pending when there is no running engine", async () => {
  expect(await restartSavedModelProfile(async () => false, "保存")).toEqual({ kind: "pending", message: "模型配置已保存，下次启动本机引擎时生效" });
});
it("does not turn a successful save into a failed save or retry persistence when restart throws", async () => {
  const restart = vi.fn(async () => { throw new Error("runtime failed"); });
  const result = await restartSavedModelProfile(restart, "保存");
  expect(result.kind).toBe("pending");
  expect(result.message).toContain("模型配置已保存");
  expect(result.message).toContain("重启失败");
  expect(restart).toHaveBeenCalledTimes(1);
});
it("preserves the completed clear operation if restarting fails", async () => {
  const result = await restartSavedModelProfile(async () => { throw new Error("offline"); }, "清除");
  expect(result.kind).toBe("pending");
  expect(result.message).toContain("已清除");
});
