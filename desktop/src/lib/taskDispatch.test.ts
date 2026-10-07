import { describe, expect, it } from "vitest";
import { DispatchAttempt, chainBudgetSelection, dependencyCandidates, dependencySelectionCurrent, dispatchTaskIdentity } from "./taskDispatch";
import type { TaskItem } from "../types";

const input = { prompt: "分析认证", agent: "", acceptance: ["给出证据"], max_steps: 8, timeout_seconds: 300 };

describe("dispatch retry identity", () => {
  it("keeps the accepted request identity after ambiguous failures, until completion", () => {
    const attempt = new DispatchAttempt();
    let counter = 0;
    const newId = () => `req-${++counter}`;
    expect(attempt.prepare("runtime:session", input, newId).request_id).toBe("req-1");
    expect(attempt.prepare("runtime:session", { ...input }, newId).request_id).toBe("req-1");
    attempt.complete("runtime:session");
    expect(attempt.prepare("runtime:session", input, newId).request_id).toBe("req-2");
  });

  it("does not reuse identity across runtime, session or contract changes", () => {
    const attempt = new DispatchAttempt();
    let counter = 0;
    const newId = () => `req-${++counter}`;
    attempt.prepare("runtime:session", input, newId);
    expect(attempt.prepare("other:session", input, newId).request_id).toBe("req-2");
    expect(attempt.prepare("other:new-session", input, newId).request_id).toBe("req-3");
    expect(attempt.prepare("other:new-session", { ...input, acceptance: ["新要求"] }, newId).request_id).toBe("req-4");
  });

  it("retains an ambiguous request when visiting and submitting in another scope", () => {
    const attempt = new DispatchAttempt();
    let counter = 0;
    const newId = () => `req-${++counter}`;
    const original = attempt.prepare("project-a:owner", input, newId).request_id;
    attempt.prepare("project-b:owner", input, newId);
    attempt.complete("project-b:owner");
    expect(attempt.prepare("project-a:owner", input, newId).request_id).toBe(original);
  });
});

describe("service task actions", () => {
  const task: TaskItem = { id: "task-dispatch-abc", kind: "delegation", status: "done", owner_session: "sid-owner",
    collaboration: { assignee: "", round: 1, review: "pending", acceptance: [], tainted: true, messages: [],
      dispatch: { source: "api", max_steps: 8, timeout_seconds: 300 } } };

  it("uses the task owner and exact round rather than the active UI session", () => {
    expect(dispatchTaskIdentity(task)).toEqual({ session: "owner", round: 1 });
  });

  it("does not mutate legacy, model-owned or malformed tasks through the service", () => {
    for (const altered of [
      { ...task, kind: "dev" }, { ...task, id: "../task-dispatch-abc" }, { ...task, owner_session: "agent-local" },
      { ...task, collaboration: { ...task.collaboration!, dispatch: undefined } },
      { ...task, collaboration: { ...task.collaboration!, round: 0 } },
      { ...task, collaboration: { ...task.collaboration!, round: 4 } },
    ]) expect(dispatchTaskIdentity(altered)).toBeNull();
  });
});

describe("dependency selections", () => {
  const own: TaskItem = { id: "task-dispatch-one", kind: "delegation", status: "done", owner_session: "sid-owner", prompt: "配置调查",
    collaboration: { assignee: "", round: 1, review: "pending", acceptance: [], tainted: true, messages: [],
      dispatch: { source: "api", max_steps: 4, timeout_seconds: 30 } } };
  const refs = [{ task_id: own.id, round: 1 }];
  it("offers only current-session service research tasks", () => {
    expect(dependencyCandidates([own, { ...own, id: "task-other", owner_session: "sid-other" },
      { ...own, kind: "dev" }, { ...own, status: "failed" }], "owner")).toEqual([
        { task_id: own.id, round: 1, prompt: "配置调查", status: "done" },
      ]);
  });
  it("does not silently advance a selected dependency to a new round or owner", () => {
    expect(dependencySelectionCurrent(refs, [own], "owner")).toBe(true);
    expect(dependencySelectionCurrent(refs, [{ ...own, collaboration: { ...own.collaboration!, round: 2 } }], "owner")).toBe(false);
    expect(dependencySelectionCurrent(refs, [own], "other")).toBe(false);
    expect(dependencySelectionCurrent(refs, [], "owner")).toBe(false);
  });
  it("rejects duplicate or excessive requirements before submission", () => {
    expect(dependencySelectionCurrent([...refs, ...refs], [own], "owner")).toBe(false);
    expect(dependencySelectionCurrent(Array.from({ length: 9 }, (_, i) => ({ task_id: `task-${i}`, round: 1 })), [], "owner")).toBe(false);
  });
  it("keeps retry identity when the dependency contract is unchanged", () => {
    const attempt = new DispatchAttempt();
    const input = { prompt: "迁移调查", agent: "", acceptance: [], max_steps: 4, timeout_seconds: 30, depends_on: refs };
    const first = attempt.prepare("runtime:owner", input, () => "req-one");
    expect(attempt.prepare("runtime:owner", { ...input, depends_on: [...refs] }, () => "req-two").request_id).toBe(first.request_id);
    expect(attempt.prepare("runtime:owner", { ...input, depends_on: [{ task_id: own.id, round: 2 }] }, () => "req-three").request_id).toBe("req-three");
  });
});

describe("task chain allowance selection", () => {
  const task: TaskItem = { id: "task-child", kind: "delegation", status: "done", owner_session: "sid-owner",
    collaboration: { assignee: "", round: 1, review: "pending", acceptance: [], tainted: true, messages: [],
      dispatch: { source: "api", max_steps: 4, timeout_seconds: 30 } },
    chain_budget: { available: true, mode: "execution_allowance", root_task_id: "task-root", token_cost_hard_limit: false } };
  const refs = [{ task_id: task.id, round: 1 }];
  it("inherits one budget across siblings and exposes distinct roots before submission", () => {
    const other = { ...task, id: "task-other" };
    const selection = [...refs, { task_id: other.id, round: 1 }];
    expect(chainBudgetSelection(selection, [task, other], "owner")).toEqual({ roots: ["task-root"], invalid: false });
    expect(chainBudgetSelection(selection, [task, { ...other, chain_budget: { ...task.chain_budget, root_task_id: "task-second-root" } }], "owner").roots).toHaveLength(2);
  });
  it("rejects stale scope and broken budget projections while accepting old unbudgeted tasks", () => {
    expect(chainBudgetSelection(refs, [task], "other").invalid).toBe(true);
    expect(chainBudgetSelection([{ ...refs[0], round: 2 }], [task], "owner").invalid).toBe(true);
    expect(chainBudgetSelection(refs, [{ ...task, chain_budget: { available: false, error: "合同损坏" } }], "owner").invalid).toBe(true);
    expect(chainBudgetSelection(refs, [{ ...task, chain_budget: {} }], "owner")).toEqual({ roots: [], invalid: false });
  });
  it("keeps retry identity for the same root limits and changes it for a changed contract", () => {
    const attempt = new DispatchAttempt();
    const contract = { ...input, chain_limits: { tasks: 8, rounds: 12, steps: 64, timeout_seconds: 1800 } };
    expect(attempt.prepare("owner", contract, () => "first").request_id).toBe("first");
    expect(attempt.prepare("owner", { ...contract }, () => "second").request_id).toBe("first");
    expect(attempt.prepare("owner", { ...contract, chain_limits: { ...contract.chain_limits, rounds: 6 } }, () => "third").request_id).toBe("third");
  });
});
