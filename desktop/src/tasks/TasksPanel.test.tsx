import { renderToStaticMarkup } from "react-dom/server";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { TasksPanel } from "./TasksPanel";

function props(): ComponentProps<typeof TasksPanel> {
  return {
    tasks: [], focusedTaskId: "", worktreeWorkspace: { worktrees: [], plans: [] },
    onFocusTask: vi.fn(),
    dispatchForm: { value: "任务目标", onChange: vi.fn(), enabled: false,
      dispatch: { mode: "dev", setMode: vi.fn(), agent: "", setAgent: vi.fn(),
        criteria: "", setCriteria: vi.fn(), maxSteps: 8, setMaxSteps: vi.fn(),
        capabilities: null, busy: false, error: "", setError: vi.fn(), capabilityError: "", submit: vi.fn(),
        requirements: [], setRequirements: vi.fn(), dependencyCandidates: [], budgetEnabled: false,
        setBudgetEnabled: vi.fn(), inheritedBudget: { roots: [], invalid: false } } },
    cardActions: { taskReviewVerifying: false, loadPlanGraph: vi.fn(), onPause: vi.fn(),
      onCancel: vi.fn(), onResume: vi.fn(), onCopy: vi.fn(), onReviewBranch: vi.fn(),
      onVerify: vi.fn(), onOpenPr: vi.fn(), onCollaboration: vi.fn(),
      getClient: () => null, onDispatchUpdated: vi.fn() },
  };
}

describe("task panel server state and action gates", () => {
  it("keeps dispatch disabled offline or outside a project", () => {
    const markup = renderToStaticMarkup(<TasksPanel {...props()} />);
    expect(markup).toContain("暂无后台任务");
    expect(markup).toContain('<button disabled="">后台开发</button>');
  });

  it("does not infer acceptance or offer development delivery for delegated results", () => {
    const input = props();
    input.tasks = [{ id: "research-result", kind: "delegation", status: "done", owner_session: "sid-owner",
      collaboration: { assignee: "reviewer", round: 2, review: "pending", acceptance: [],
        tainted: true, messages: [] }, result: "结果证据" }];
    input.focusedTaskId = "research-result";
    const markup = renderToStaticMarkup(<TasksPanel {...input} />);
    expect(markup).toContain('class="task-card focused"');
    expect(markup).toContain("执行结束 · 待验收");
    expect(markup).toContain("结果证据");
    expect(markup).not.toContain("开 Draft PR");
    expect(markup).not.toContain("目标已达成");
  });

  it.each([
    ["pending", true, "交接已处理", "执行结束 · 待验收"],
    ["accepted", false, "结果待处理", "已验收"],
  ] as const)("shows handling independently of review %s", (review, handled, label, reviewLabel) => {
    const input = props();
    input.tasks = [{ id: "service-result", kind: "delegation", status: "done", owner_session: "sid-owner",
      collaboration: { assignee: "reviewer", round: 1, review, acceptance: [], tainted: true, messages: [] },
      handoff: { completed: [], remaining: [], next_action: "汇报", text: "结果",
        handling: { revision: "revision", handled, handled_at: handled ? "timestamp" : "" } } }];
    const markup = renderToStaticMarkup(<TasksPanel {...input} />);
    expect(markup).toContain(label);
    expect(markup).toContain(reviewLabel);
  });

  it("keeps stale verification blocking a development PR after panel extraction", () => {
    const input = props();
    input.tasks = [{ id: "dev-result", status: "done", branch: "work/result",
      branch_review: { verification_stale: true, accepted_hunks: 1 } }];
    const markup = renderToStaticMarkup(<TasksPanel {...input} />);
    expect(markup).toContain("审查后待重验");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>开 Draft PR<\/button>/);
  });

  it("offers a separate manual receipt for an unread result, with offline submit disabled", () => {
    const input = props();
    input.tasks = [{ id: "task-result", kind: "dev", status: "done", owner_session: "sid-owner",
      handoff: { completed: [], remaining: [], next_action: "汇报", text: "结果",
        handling: { revision: "a".repeat(64), handled: false, handled_at: "" } } }];
    const markup = renderToStaticMarkup(<TasksPanel {...input} />);
    expect(markup).toContain("记录交接处理");
    expect(markup).toContain("任务验收单独记录");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>标记已处理<\/button>/);
    input.tasks[0].handoff!.handling!.handled = true;
    const handled = renderToStaticMarkup(<TasksPanel {...input} />);
    expect(handled).toContain("交接已处理");
    expect(handled).not.toContain("标记已处理");
  });

  it("shows a blocked question with answer and cancel controls, separately from completed work", () => {
    const input = props();
    input.tasks = [{ id: "task-question", kind: "delegation", status: "blocked", owner_session: "sid-owner",
      collaboration: { assignee: "reader", round: 1, review: "not_submitted", acceptance: [], tainted: true, messages: [],
        dispatch: { source: "api", max_steps: 4, timeout_seconds: 30 },
        questions: [{ id: "question-first", task_id: "task-question", round: 1, status: "open", question: "哪个环境？",
          options: ["测试", "生产"], context: "已找到配置", asked_by: "reader", answerer: "owner",
          created: new Date().toISOString(), expires_at: new Date(Date.now() + 86400000).toISOString(), answer: "", answered_at: "" }] } }];
    const markup = renderToStaticMarkup(<TasksPanel {...input} />);
    expect(markup).toContain("等待回答");
    expect(markup).toContain("哪个环境？");
    expect(markup).toContain("已找到配置");
    expect(markup).toContain("取消研究任务");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>回答并继续<\/button>/);
    expect(markup).not.toContain("记录验收通过");
    expect(markup).not.toContain("标记已处理");
  });

  it.each(["accepted", "pending"] as const)("preserves stale historical results without actionable review %s", (review) => {
    const input = props();
    input.tasks = [{ id: "task-stale", kind: "delegation", status: "done", owner_session: "sid-owner",
      collaboration: { assignee: "reader", round: 1, review, acceptance: [], tainted: true, messages: [],
        dispatch: { source: "api", max_steps: 4, timeout_seconds: 30 } }, result: "旧结果证据",
      dependencies: { version: 1, task_id: "task-stale", owner_session: "sid-owner", resolution: "consumed",
        requires: [{ task_id: "task-source", round: 1 }], result_valid: false, can_reconcile: true, failure: "前置版本变化" } }];
    const markup = renderToStaticMarkup(<TasksPanel {...input} />);
    expect(markup).toContain("依赖已失效 · 历史记录保留");
    expect(markup).toContain("旧结果证据");
    expect(markup).toContain("核对并记录依赖失效");
    expect(markup).not.toContain("记录验收通过");
    expect(markup).not.toContain("交给主 Agent 验收");
    expect(markup).not.toContain("补充并执行下一轮");
  });

  it("shows stop coordination without answering a question from an invalidated execution", () => {
    const input = props();
    input.tasks = [{ id: "task-stopping", kind: "delegation", status: "blocked", owner_session: "sid-owner",
      collaboration: { assignee: "reader", round: 1, review: "not_submitted", acceptance: [], tainted: true, messages: [],
        dispatch: { source: "api", max_steps: 4, timeout_seconds: 30 } },
      dependencies: { version: 1, task_id: "task-stopping", owner_session: "sid-owner", resolution: "invalidated",
        requires: [{ task_id: "task-source", round: 1 }], result_valid: false, can_reconcile: true, stop_pending: true } }];
    const markup = renderToStaticMarkup(<TasksPanel {...input} />);
    expect(markup).toContain("停止尚未确认");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>核对失效并停止<\/button>/);
    expect(markup).not.toContain("回答并继续");
  });
});
