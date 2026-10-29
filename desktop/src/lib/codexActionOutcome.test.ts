import { expect, it } from "vitest";

import type { CodexAction } from "../types";
import { chatCodexActions, codexActionOutcome } from "./codexActionOutcome";

const NOW = Date.parse("2026-10-29T05:30:00Z");
const action = (id: string, status: CodexAction["status"], minutesAgo: number, extra: Partial<CodexAction> = {}): CodexAction => ({
  id, thread_id: "th-a1e7fb", thread_name: "实现 add 函数", kind: "message", text: "x", status, tainted: false,
  session: "sid-main", error: "", created_at: "", updated_at: new Date(NOW - minutesAgo * 60_000).toISOString(), ...extra });

it("keeps what still needs you and fresh outcomes, drops old finished cards", () => {
  const shown = chatCodexActions([
    action("waiting", "proposed", 120), action("checking", "sending", 90), action("unclear", "unknown", 300),
    action("fresh", "sent", 3), action("old-sent", "sent", 40), action("old-failed", "failed", 200),
    action("legacy-unclear", "failed", 200, { error: "Codex 响应超时，发送结果无法确认" }),
    action("dropped", "cancelled", 1),
  ], "main", NOW).map(item => item.id);
  expect(shown).toEqual(["waiting", "checking", "unclear", "fresh", "legacy-unclear"]);
});

it("shows only this conversation's actions, and supervisor drafts only while they wait", () => {
  const shown = chatCodexActions([
    action("other-chat", "proposed", 1, { session: "sid-side" }),
    action("supervisor-waiting", "proposed", 60, { session: "" }),
    action("supervisor-sent", "sent", 1, { session: "" }),
  ], "main", NOW).map(item => item.id);
  expect(shown).toEqual(["supervisor-waiting"]);
});

it("reads an older host's uncertain failure as unknown", () => {
  expect(codexActionOutcome({ status: "failed", error: "请求可能已送达，但 Codex 回执无法确认" })).toBe("unknown");
  expect(codexActionOutcome({ status: "failed", error: "Codex 拒绝 thread/turns/list" })).toBe("failed");
});
