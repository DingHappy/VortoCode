import { expect, it } from "vitest";
import { restartSavedSetting } from "./settingsApply";
it("only reports applied after a confirmed restart", async () => {
  expect(await restartSavedSetting(async () => true, "配置已开启")).toEqual({ kind: "saved", message: "配置已开启，本机引擎已重启" });
});
it("does not claim an already written setting failed when restart throws", async () => {
  const value = await restartSavedSetting(async () => { throw Error("runtime stopped"); }, "配置已关闭");
  expect(value.kind).toBe("pending"); expect(value.message).toContain("配置已关闭"); expect(value.message).toContain("重启失败");
});
it("keeps application pending when there is no running local engine", async () => {
  expect(await restartSavedSetting(async () => false, "配置已保存")).toEqual({ kind: "pending", message: "配置已保存，下次启动本机引擎时生效" });
});
