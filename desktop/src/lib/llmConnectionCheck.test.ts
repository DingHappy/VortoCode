import { expect, it } from "vitest";
import type { LlmConnectionTest } from "../types";
import { createConnectionCheck } from "./llmConnectionCheck";
import type { ConnectionCheckState, ConnectionDraft } from "./llmConnectionCheck";

function deferred() {
  let resolve!: (value: LlmConnectionTest) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<LlmConnectionTest>((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
const result: LlmConnectionTest = { ok: true, status: 200, modelCount: 1, modelAvailable: true, models: ["local-model"], message: "列表可达" };
function fixture() {
  let draft: ConnectionDraft = { baseUrl: "http://127.0.0.1:11500/v1", apiKey: "fixture-token", model: "local-model" };
  const pending = deferred();
  const states: ConnectionCheckState[] = [];
  const candidates: string[][] = [];
  const check = createConnectionCheck(() => draft, () => pending.promise, state => states.push(state), values => candidates.push(values));
  return { pending, states, candidates, check, change: (value: Partial<ConnectionDraft>) => { draft = { ...draft, ...value }; } };
}
it("publishes current reachability and candidates without saving or inference", async () => {
  const f = fixture(); const request = f.check.test();
  f.pending.resolve(result); await request;
  expect(f.states[f.states.length - 1]).toMatchObject({ testing: false, result });
  expect(f.candidates).toEqual([["local-model"]]);
});
for (const change of [{baseUrl: "http://127.0.0.1:8000/v1"}, {apiKey: "replacement"}, {model: "other-model"}]) {
  it(`rejects late success after ${Object.keys(change)[0]} changes`, async () => {
    const f = fixture(); const request = f.check.test(); f.change(change);
    f.pending.resolve(result); await request;
    expect(f.states).toHaveLength(1); expect(f.candidates).toEqual([]);
  });
}
it("reset prevents stale errors and results after unmount or draft change-back", async () => {
  const f = fixture(); const request = f.check.test(); f.check.reset();
  f.pending.reject(new Error("old failure")); await request;
  expect(f.states).toHaveLength(1); expect(f.candidates).toEqual([]);
});
it("an older completion cannot clear a newer attempt's busy state", async () => {
  const first = deferred(); const second = deferred(); let count = 0;
  const states: ConnectionCheckState[] = [];
  const check = createConnectionCheck(() => ({baseUrl: "local", apiKey: "", model: "m"}),
    () => ++count === 1 ? first.promise : second.promise, state => states.push(state), () => {});
  const a = check.test(); const b = check.test(); first.resolve(result); await a;
  expect(states).toHaveLength(2); expect(states[states.length - 1]?.testing).toBe(true);
  second.resolve(result); await b; expect(states[states.length - 1]?.testing).toBe(false);
});
it("current failures remain retryable without publishing model candidates", async () => {
  const f = fixture(); const request = f.check.test(); f.pending.reject(new Error("401 unauthorized")); await request;
  expect(f.states[f.states.length - 1]).toMatchObject({testing: false, result: null, error: "401 unauthorized"});
  expect(f.candidates).toEqual([]);
});
