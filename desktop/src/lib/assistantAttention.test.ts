import { expect, it } from "vitest";
import { assistantAttention } from "./assistantAttention";
import { codexActionFeedItem, confirmationFeedItem, dispatchFeedItem, noticeFeedItems, taskFeedItem, type FeedItem } from "./activityFeed";
import type { AssistantDispatch, CodexAction } from "../types";

const item = (id: string, status: FeedItem["status"], extra: Partial<FeedItem> = {}): FeedItem =>
  ({ id, title: id, summary: id, status, at: "2026-11-01T10:00:00Z", ...extra });

it("keeps every decision and exception even when recent results are bounded", () => {
  const decisions = Array.from({ length: 8 }, (_, i) => item(`waiting-${i}`, "waiting", { presentation: "history" }));
  const failures = Array.from({ length: 5 }, (_, i) => item(`failed-${i}`, "failed", { presentation: "history" }));
  const results = Array.from({ length: 10 }, (_, i) => item(`result-${i}`, "done", { taskId: `t-${i}`, at: `2026-11-01T10:${String(i).padStart(2, "0")}:00Z` }));
  const view = assistantAttention([...decisions, ...failures, ...results]);
  expect(view.decisions).toHaveLength(8);
  expect(view.exceptions).toHaveLength(5);
  expect(view.recentResults.map(row => row.id)).toEqual(["result-9", "result-8", "result-7"]);
  expect(view.additionalResults).toBe(7);
});

it("does not promote chat replies, progress logs or accepted requests into deliveries", () => {
  const source = [item("chat", "done", { kind: "chat", statusLabel: "已回复" }),
    item("receipt", "done", { codexActionId: "a", presentation: "history", statusLabel: "请求已受理" }),
    item("progress", "running"), item("review", "done", { dispatchId: "d", statusLabel: "执行结束 · 待审查" })];
  const before = [...source];
  const view = assistantAttention(source);
  expect(view.recentResults).toEqual([source[3]]);
  expect(view.recentResults[0].statusLabel).toBe("执行结束 · 待审查");
  expect(view.running).toBe(1);
  expect(source).toEqual(before);
});

it("uses explicit adapter classifications for briefings, recovered services and routine messages", () => {
  const at = "2026-11-01T10:00:00Z";
  const source = noticeFeedItems([
    { ts: at, source: "assistant", text: "🗒 简报\n检查通过，尚未部署。" },
    { ts: at, source: "ops:demo", text: "普通运行记录" },
    { ts: at, source: "ops:demo", text: "✅ 服务已恢复" },
    { ts: at, source: "cron:daily", text: "日报\n执行结束" },
    { ts: at, source: "ops:broken", text: "🔴 服务异常" },
  ]);
  const view = assistantAttention(source);
  expect(view.recentResults.map(row => row.sourceLabel)).toEqual(["助理简报", "运维报到", "定时任务"]);
  expect(view.exceptions.map(row => row.title)).toEqual(["运维 · broken"]);
  expect(view.recentResults).not.toContain(source[1]);
});

it("keeps uncertain sends and active confirmations visible, but puts expired actions in history", () => {
  const base: CodexAction = { id: "a", thread_id: "thread", thread_name: "demo", kind: "message", text: "检查", tainted: false,
    session: "s", error: "", status: "proposed", created_at: "", updated_at: "" };
  const unknown = codexActionFeedItem({ ...base, status: "sending" })!;
  const expired = codexActionFeedItem({ ...base, id: "old", status: "expired" })!;
  const future = codexActionFeedItem({ ...base, id: "future", status: "future" as CodexAction["status"] })!;
  const confirmation = confirmationFeedItem({ id: "confirm", text: "允许？", tainted: false, responseState: "awaiting" });
  const view = assistantAttention([unknown, expired, confirmation, item("interrupted", "stopped"), future]);
  expect(view.decisions).toEqual([unknown, confirmation]);
  expect(view.exceptions.map(row => row.id)).toEqual(["interrupted", "codex_action:future"]);
  expect(view.recentResults).toEqual([]);
});

it("puts explicit cancellation in history while retaining interruption and failure as exceptions", () => {
  const cancelled = taskFeedItem({ id: "cancelled", status: "cancelled" });
  const interrupted = taskFeedItem({ id: "interrupted", status: "interrupted" });
  const failed = taskFeedItem({ id: "failed", status: "failed" });
  const view = assistantAttention([cancelled, interrupted, failed]);
  expect(cancelled.statusLabel).toBe("已取消");
  expect(view.exceptions).toEqual([interrupted, failed]);
  expect(view.recentResults).toEqual([]);
});

it("retains records with missing timestamps without treating them as new deliveries", () => {
  const view = assistantAttention([item("no-time", "done", { taskId: "t1", at: "invalid" }),
    item("known-time", "done", { taskId: "t2" }), item("unknown-stop", "stopped", { at: "" })]);
  expect(view.recentResults.map(row => row.id)).toEqual(["known-time", "no-time"]);
  expect(view.exceptions.map(row => row.id)).toEqual(["unknown-stop"]);
});

it("surfaces unreachable dispatch sources even when their retained state says running or done", () => {
  const base: AssistantDispatch = { id: "d", project: "demo", title: "核对发布", goal: "检查", criteria: [], status: "done",
    result: "上次结果", error: "", tainted: false, remote_task_id: "t", branch: "", created_at: "", updated_at: "", sync_state: "unreachable" };
  const retainedDone = dispatchFeedItem(base)!;
  const retainedRunning = dispatchFeedItem({ ...base, id: "running", status: "running" })!;
  const view = assistantAttention([retainedDone, retainedRunning]);
  expect(view.exceptions).toEqual([retainedDone, retainedRunning]);
  expect(view.recentResults).toEqual([]);
  expect(view.running).toBe(0);
  expect(retainedDone.statusLabel).toBe("状态待核对");
});
