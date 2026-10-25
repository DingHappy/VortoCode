import { describe, expect, it } from "vitest";

import type { GatewayProcessStatus } from "../types";
import { observeCurrentRuntime, pruneRuntimeTokens } from "./runtimeSupervision";

const process = (runtimeId: string, running: boolean): GatewayProcessStatus => ({ runtimeId, running, message: "" });

describe("runtime supervision", () => {
  it("drops tokens of runtimes that are gone or stopped", () => {
    const tokens = new Map([["a", "ta"], ["b", "tb"], ["c", "tc"]]);
    pruneRuntimeTokens(tokens, [process("a", true), process("b", false)]);
    expect([...tokens.keys()]).toEqual(["a"]);
  });

  it("only a runtime that was running and is no longer counts as an unexpected stop", () => {
    expect(observeCurrentRuntime([process("a", false)], "a", true).unexpectedlyStopped).toBe(true);
    expect(observeCurrentRuntime([process("a", false)], "a", false).unexpectedlyStopped).toBe(false);
    expect(observeCurrentRuntime([process("a", true)], "a", true).unexpectedlyStopped).toBe(false);
  });

  it("a runtime missing from the list is reported as stopped", () => {
    const { status, unexpectedlyStopped } = observeCurrentRuntime([process("b", true)], "a", true);
    expect(status).toMatchObject({ runtimeId: "a", running: false, message: "当前 runtime 已停止" });
    expect(unexpectedlyStopped).toBe(true);
  });
});
