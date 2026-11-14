import { expect, it } from "vitest";
import { createdChannel, sessionChannel } from "./sessionChannels";
import type { SessionSummary } from "../types";

const row: SessionSummary = { sid: "a1", title: "资料研究", messages: 0, updated: 1, channel: { version: 1, topic: "公开资料" } };

it("recognizes only versioned display metadata, preserving ordinary and legacy chats", () => {
  expect(sessionChannel(row)).toEqual({ version: 1, topic: "公开资料" });
  expect(sessionChannel(undefined)).toBeNull();
  expect(sessionChannel({})).toBeNull();
  for (const channel of [true, [], { version: 2, topic: "" }, { version: 1, topic: 3 },
    { version: 1, topic: "x".repeat(301) }, { version: 1, topic: "", permissions: ["deliver"] }]) {
    expect(sessionChannel({ channel } as SessionSummary)).toBeNull();
  }
});

it("requires a complete saved-channel receipt before selecting its SID, without guessing or retrying", () => {
  expect(createdChannel({ session: row })).toEqual(row);
  for (const value of [null, row, { session: { ...row, sid: "../other" } }, { session: { ...row, title: "" } },
    { session: { ...row, messages: 1 } }, { session: { ...row, updated: NaN } }, { session: { ...row, channel: undefined } }]) {
    expect(() => createdChannel(value)).toThrow("未自动重试");
  }
});
