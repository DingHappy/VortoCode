import { test } from "node:test";
import assert from "node:assert/strict";

import { commitOnMain } from "./release-commit.mjs";

const git = (fetch, ancestor) => {
  const calls = [];
  const run = (args) => {
    calls.push(args.join(" "));
    return { status: args[0] === "fetch" ? fetch : ancestor };
  };
  return { run, calls };
};

test("a commit already merged into origin/main passes", () => {
  const { run, calls } = git(0, 0);
  assert.deepEqual(commitOnMain(run, "abc123"), { onMain: true, reason: null });
  assert.deepEqual(calls, ["fetch --quiet origin main", "merge-base --is-ancestor abc123 origin/main"]);
});

test("a commit only on a PR branch fails and says to merge first", () => {
  const { run } = git(0, 1);
  const verdict = commitOnMain(run, "abc123");
  assert.equal(verdict.onMain, false);
  assert.match(verdict.reason, /not on origin\/main; merge the PR first/);
});

test("fails closed when origin cannot be fetched or compared", () => {
  const offline = git(128, 0);
  assert.equal(commitOnMain(offline.run, "abc123").onMain, false);
  assert.deepEqual(offline.calls, ["fetch --quiet origin main"]);
  assert.equal(commitOnMain(git(0, 128).run, "abc123").onMain, false);
});
