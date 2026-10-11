import { expect, it } from "vitest";
import { createGoalNavigation } from "./goals";
import type { GoalFocus } from "./goals";
import type { GoalItem } from "../types";
const goal = (id: string): GoalItem => ({ id, objective: id, status: "draft", acceptance_criteria: [], constraints: [], non_goals: [], task_ids: [], evidence: [], progress: {passed: 0, failed: 0, total: 0} });
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject}; }
it("later target wins over an earlier success or error", async () => {
  const first = deferred<GoalItem>(); const second = deferred<GoalItem>();
  const client = {getGoal: (id: string) => id === "a" ? first.promise : second.promise};
  let state: GoalFocus | null = null;
  const navigation = createGoalNavigation(() => client, value => {state = value;});
  const a = navigation.focus("a"); const b = navigation.focus("b");
  second.resolve(goal("b")); await b; first.reject(new Error("old failure")); await a;
  expect(state).toEqual({id: "b", status: "ready", goal: goal("b")});
});
it("scope reset rejects late responses even when the same client and id return", async () => {
  const pending = deferred<GoalItem>(); const client = {getGoal: () => pending.promise};
  let state: GoalFocus | null = null;
  const navigation = createGoalNavigation(() => client, value => {state = value;});
  const request = navigation.focus("same"); navigation.reset(); pending.resolve(goal("same")); await request;
  expect(state).toBeNull();
});
it("a new project's identical id cannot receive an old client's result", async () => {
  const pending = deferred<GoalItem>(); let client = {getGoal: () => pending.promise};
  const states: Array<GoalFocus | null> = [];
  const navigation = createGoalNavigation(() => client, value => states.push(value));
  const request = navigation.focus("same"); client = {getGoal: async () => goal("same")};
  pending.resolve(goal("same")); await request;
  expect(states).toHaveLength(1); expect(states[0]?.status).toBe("loading");
  await navigation.focus("same"); expect(states[states.length - 1]?.status).toBe("ready");
});
it("404 or mismatched identity remains unverified and supports explicit retry", async () => {
  let mode = 0; let state: GoalFocus | null = null;
  const client = {getGoal: async () => {if (mode === 0) throw new Error("404 Goal not found"); return goal(mode === 1 ? "wrong" : "target");}};
  const navigation = createGoalNavigation(() => client, value => {state = value;});
  await navigation.focus("target"); expect(state).toMatchObject({status: "error", error: "404 Goal not found"});
  mode = 1; await navigation.focus("target"); expect(state).toMatchObject({status: "error"});
  mode = 2; await navigation.focus("target"); expect(state).toMatchObject({status: "ready", id: "target"});
});
it("no connection does not expose a remembered Goal as verified", async () => {
  let state: GoalFocus | null = null;
  await createGoalNavigation(() => null, value => {state = value;}).focus("target");
  expect(state).toMatchObject({id: "target", status: "error"});
});
it("a late deletion clears only its own selection and cannot dismiss a newer target", async () => {
  let state: GoalFocus | null = null;
  const client = {getGoal: async (id: string) => goal(id)};
  const navigation = createGoalNavigation(() => client, value => {state = value;});
  await navigation.focus("old"); await navigation.focus("new"); navigation.clearDeleted("old");
  expect(state).toMatchObject({id: "new", status: "ready"});
  navigation.clearDeleted("new"); expect(state).toBeNull();
});
