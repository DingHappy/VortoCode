import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createBuildInfo } from "./build-info.mjs";

test("source changes, rebuilds and version mismatches remain distinguishable", () => {
  const root = mkdtempSync(join(tmpdir(), "vorto-build-info-")), desktop = join(root, "desktop");
  mkdirSync(join(desktop, "src-tauri"), { recursive: true }); mkdirSync(join(desktop, "src"));
  writeFileSync(join(desktop, "package.json"), '{"version":"0.1.1"}');
  writeFileSync(join(desktop, "src-tauri/tauri.conf.json"), '{"version":"0.1.1"}');
  writeFileSync(join(desktop, "src/app.ts"), "first");
  const date = new Date("2026-10-09T00:00:00Z");
  try {
    const first = createBuildInfo(desktop, date);
    assert.deepEqual(createBuildInfo(desktop, date), first);
    writeFileSync(join(root, ".env"), "private fixture value");
    assert.deepEqual(createBuildInfo(desktop, date), first);
    const rebuilt = createBuildInfo(desktop, new Date("2026-10-09T00:00:01Z"));
    assert.equal(rebuilt.sourceFingerprint, first.sourceFingerprint); assert.notEqual(rebuilt.buildId, first.buildId);
    writeFileSync(join(desktop, "src/app.ts"), "changed without a commit");
    assert.notEqual(createBuildInfo(desktop, date).sourceFingerprint, first.sourceFingerprint);
    writeFileSync(join(desktop, "package.json"), '{"version":"0.1.2"}');
    assert.throws(() => createBuildInfo(desktop, date), /versions differ/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
