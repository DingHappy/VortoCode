import { describe, expect, it } from "vitest";

import { confirmAction } from "./confirm";

describe("confirmAction", () => {
  it("passes the message to the native confirm command and allows only an explicit true", async () => {
    const calls: Array<[string, Record<string, unknown>]> = [];
    const allow = await confirmAction("撤销 a.py 的本地修改？", async (command, args) => {
      calls.push([command, args]);
      return true;
    });
    expect(allow).toBe(true);
    expect(calls).toEqual([["confirm_action", { message: "撤销 a.py 的本地修改？" }]]);
  });

  it("treats cancel as a refusal", async () => {
    expect(await confirmAction("删除？", async () => false)).toBe(false);
  });

  it("fails closed on anything other than a boolean true", async () => {
    // 原 bug 就是把"真值"当成"同意"；这里只认严格的 true。
    for (const value of ["true", 1, {}, undefined, null]) {
      expect(await confirmAction("删除？", async () => value)).toBe(false);
    }
  });

  it("fails closed when the native dialog cannot be shown", async () => {
    expect(await confirmAction("删除？", async () => {
      throw new Error("command confirm_action not found");
    })).toBe(false);
  });
});
