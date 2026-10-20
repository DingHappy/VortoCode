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

it("a waiting task shows its question with suggested answers; legacy tasks point to the workbench", () => {
  const asking: AssistantDispatch = { ...task, tainted: false, status: "waiting", remote_task_id: "t1", remote_session: "assistant-a",
    question: { id: "q1", round: 1, question: "超时改成 30 秒还是 60 秒？", options: ["30 秒", "60 秒"], context: "找到了配置",
      expires_at: "2026-10-13T00:00:00Z" } };
  const html = renderToStaticMarkup(<AssistantTaskCard task={asking} connected onAction={async () => {}} />);
  for (const text of ["任务在问你", "30 秒还是 60 秒", "60 秒", "你的回答", "交给任务接着做", "找到了配置"]) expect(html).toContain(text);
  expect(assistantTaskActions(asking).answer).toBe(true);
  const legacy = renderToStaticMarkup(<AssistantTaskCard task={{ ...asking, remote_session: undefined }} connected onAction={async () => {}} />);
  expect(legacy).toContain("请打开项目任务回答");
  expect(legacy).not.toContain("交给任务接着做");
});

it("a finished vorto branch offers a draft PR once; the link is shown after and only https is linked", () => {
  const done: AssistantDispatch = { ...task, tainted: false, status: "done", remote_task_id: "t1", branch: "vorto/fix", attempt: 2, retry_of: "dsp-0" };
  const html = renderToStaticMarkup(<AssistantTaskCard task={done} connected onAction={async () => {}} />);
  expect(html).toContain("开 draft PR");
  expect(html).toContain("第 2 次尝试");
  expect(assistantTaskActions({ ...done, branch: "feature/x" }).openPr).toBe(false);
  expect(assistantTaskActions({ ...done, error: "集成没过" }).openPr).toBe(false);
  const opening = renderToStaticMarkup(<AssistantTaskCard task={{ ...done, pr_state: "opening" }} connected onAction={async () => {}} />);
  expect(opening).toContain("正在推分支");
  expect(opening).not.toContain("开 draft PR</button>");
  const opened = renderToStaticMarkup(<AssistantTaskCard task={{ ...done, pr_url: "https://github.com/x/y/pull/9", pr_state: "opened" }} connected onAction={async () => {}} />);
  expect(opened).toContain('href="https://github.com/x/y/pull/9"');
  const sneaky = renderToStaticMarkup(<AssistantTaskCard task={{ ...done, pr_url: "javascript:alert(1)" }} connected onAction={async () => {}} />);
  expect(sneaky).not.toContain("javascript:");
  const failed = renderToStaticMarkup(<AssistantTaskCard task={{ ...done, pr_state: "failed", pr_error: "分支已被删" }} connected onAction={async () => {}} />);
  expect(failed).toContain("没开成 PR：分支已被删");
  expect(failed).toContain("开 draft PR");
});

it("shows the assistant's acceptance check and failure diagnosis without claiming acceptance", () => {
  const done: AssistantDispatch = { ...task, tainted: false, status: "done", remote_task_id: "t1", branch: "vorto/fix",
    outcome: { kind: "acceptance", criteria: [{ text: "超时是 30 秒", verdict: "met", evidence: "TIMEOUT=30" },
      { text: "补了单测", verdict: "unknown", evidence: "" }] } };
  const html = renderToStaticMarkup(<AssistantTaskCard task={done} connected onAction={async () => {}} />);
  for (const text of ["1/2 条对上", "✓ 超时是 30 秒——TIMEOUT=30", "? 补了单测", "不代表人工验收"]) expect(html).toContain(text);
  const failed = renderToStaticMarkup(<AssistantTaskCard task={{ ...task, tainted: false, status: "failed", error: "ImportError",
    outcome: { kind: "diagnosis", diagnosis: "缺测试依赖", retry: true, change: "先装依赖", draft_id: "dsp-b" } }} connected onAction={async () => {}} />);
  expect(failed).toContain("助理的判断：缺测试依赖");
  expect(failed).toContain("已拟好下一次的任务单");
  const draft = renderToStaticMarkup(<AssistantTaskCard task={{ ...task, retry_note: "先装依赖", attempt: 2 }} connected onAction={async () => {}} />);
  expect(draft).toContain("这次换个做法：先装依赖");
});

it("a running task shows how far the pipeline got, step by step", () => {
  const running: AssistantDispatch = { ...task, tainted: false, status: "running", remote_task_id: "t1",
    progress: { total: 3, landed: 1, failed: 0, more: 0, latest: "", stage: "running",
      steps: [{ title: "改超时", status: "landed" }, { title: "补单测", status: "running" }, { title: "", status: "pending" }] } };
  const html = renderToStaticMarkup(<AssistantTaskCard task={running} connected onAction={async () => {}} />);
  for (const text of ["1/3 步已落地，正在做：补单测", "✓ 改超时", "▶ 补单测", "· 第 3 步"]) expect(html).toContain(text);
  const planning = renderToStaticMarkup(<AssistantTaskCard task={{ ...running, progress: { total: 0, landed: 0, failed: 0, more: 0,
    latest: "正在拆解任务", stage: "", steps: [] } }} connected onAction={async () => {}} />);
  expect(planning).toContain("还在拆计划：正在拆解任务");
});
