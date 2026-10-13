import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { AssistantDispatch } from "../types";
import { AssistantTaskCard } from "./AssistantTaskCard";
import { assistantTaskActions } from "../lib/assistantTask";

const task: AssistantDispatch = { id: "dsp-a", project: "demo", title: "修复登录", goal: "修复超时",
  criteria: ["超时测试通过"], status: "proposed", tainted: true, remote_task_id: "", result: "", error: "",
  branch: "", created_at: "2026-10-12T00:00:00Z", updated_at: "2026-10-12T00:00:00Z" };

it("proposed card exposes the exact goal, criteria and taint before explicit dispatch", () => {
  const html = renderToStaticMarkup(<AssistantTaskCard task={task} connected onAction={async () => {}} />);
  for (const text of ["等待确认下发", "修复超时", "超时测试通过", "包含外部内容", "确认下发", "撤回任务单"]) expect(html).toContain(text);
  expect(html).not.toContain("查看交付与审查");
});

it("ambiguous submissions cannot be cancelled and legacy submissions cannot be replayed", () => {
  expect(assistantTaskActions({ ...task, status: "submission_unknown", request_id: "key" }))
    .toMatchObject({ approve: false, cancel: false, retry: true, open: false });
  expect(assistantTaskActions({ ...task, status: "submission_unknown", request_id: "key", legacy_submission: true }).retry).toBe(false);
  expect(assistantTaskActions({ ...task, status: "send_failed" }).retry).toBe(false);
  expect(assistantTaskActions({ ...task, status: "send_failed", request_id: "key", remote_task_id: "predicted-id", submission_attempted: false }))
    .toMatchObject({ retry: true, open: false, cancel: true });
});

it("delivery opens review, distinguishes completion from acceptance and reports stale status", () => {
  const html = renderToStaticMarkup(<AssistantTaskCard task={{ ...task, status: "done", remote_task_id: "t1",
    branch: "vorto/fix", sync_state: "unreachable", last_checked_at: task.created_at }} connected={false} onAction={async () => {}} />);
  for (const text of ["等待审查", "当前无法核实", "上次核对", "vorto/fix", "查看交付与审查", "不代表验收通过", "连接中断"]) expect(html).toContain(text);
  expect(html).toContain("disabled");
  expect(html).not.toContain("确认下发");
});

it("shows commit-bound verification and exact coverage without claiming acceptance", () => {
  const delivered: AssistantDispatch = { ...task, status: "done", branch: "vorto/fix", remote_task_id: "t1",
    delivery_evidence: { head: "a".repeat(40), verification: "passed", verified_head: "a".repeat(40),
      verified_at: task.created_at, checked_at: task.updated_at, coverage_known: true, accepted_hunks: 1, total_hunks: 2 } };
  const html = renderToStaticMarkup(<AssistantTaskCard task={delivered} connected onAction={async () => {}} />);
  for (const text of ["aaaaaaaaaaaa", "该提交验证通过", "1/2 已接受", "不代表验收通过"]) expect(html).toContain(text);
  const stale = renderToStaticMarkup(<AssistantTaskCard task={{ ...delivered, delivery_evidence: {
    ...delivered.delivery_evidence!, verification: "stale", coverage_known: false,
  } }} connected onAction={async () => {}} />);
  expect(stale).toContain("验证证据已过期，需要重新验证");
  expect(stale).not.toContain("该提交验证通过");
  expect(stale).not.toContain("1/2 已接受");
});

it("missing review evidence is explicitly unknown", () => {
  const html = renderToStaticMarkup(<AssistantTaskCard task={{ ...task, status: "done", branch: "vorto/fix",
    delivery_error: "review unavailable" }} connected onAction={async () => {}} />);
  expect(html).toContain("尚未核对交付证据");
  expect(html).toContain("当前无法核实交付证据");
  expect(html).not.toContain("该提交验证通过");
});

it("recovery progress preserves the original failure and does not grant execution", () => {
  const recovered: AssistantDispatch = { ...task, status: "failed", remote_task_id: "original", error: "original failed",
    recovery_task: { id: "task-resume-123", parent_task_id: "original", plan_id: "p1", status: "running", branch: "vorto/fix",
      result: "", error: "", checked_at: task.updated_at, has_further_recovery: false } };
  const html = renderToStaticMarkup(<AssistantTaskCard task={recovered} connected onAction={async () => {}} />);
  for (const text of ["original failed", "task-resume-123", "恢复进度", "执行中", "原任务记录保留"]) expect(html).toContain(text);
  expect(html).not.toContain("确认下发");
  expect(html).not.toContain("继续核对并提交");
  expect(html).not.toContain("该提交验证通过");
  const offline = renderToStaticMarkup(<AssistantTaskCard task={{ ...recovered, sync_state: "unreachable" }} connected onAction={async () => {}} />);
  expect(offline).toContain("恢复信息为上次记录，当前无法重新核实");
});
