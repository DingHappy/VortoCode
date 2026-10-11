import { test } from "node:test";
import assert from "node:assert/strict";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { installApp } from "./install-app.mjs";

for (const mode of ["success", "backup-failure", "stage-failure", "published-failure", "identity-change", "first-install"]) {
  test(`verified replacement: ${mode}`, () => {
    const root = mkdtempSync(join(tmpdir(), "vorto-install-contract-"));
    const source = join(root, "built.app"), target = join(root, "VortoCode.app");
    const payload = path => readFileSync(join(path, "payload"), "utf8");
    const make = (path, text) => { mkdirSync(path); writeFileSync(join(path, "payload"), text); };
    make(source, "new"); if (mode !== "first-install") make(target, "old");
    const inspect = path => {
      const text = payload(path);
      if (mode === "published-failure" && path === target && text === "new") throw new Error("verification failed after publish");
      return { identifier: "com.vorto.vortocode", designatedRequirement: mode === "identity-change" && text === "new" ? "different" : "stable", appSha256: text, sidecarSha256: text };
    };
    const copy = (from, to) => {
      if (mode === "backup-failure" && from === target) throw new Error("backup save failed");
      cpSync(from, to, { recursive: true });
      if (mode === "stage-failure" && from === source) writeFileSync(join(to, "payload"), "corrupted");
    };
    try {
      if (["success", "first-install"].includes(mode)) {
        const result = installApp({ source, target, inspect, copy, backupRoot: root });
        assert.equal(payload(target), "new");
        if (mode === "success") assert.equal(payload(result.backup), "old");
        else assert.equal(result.backup, null);
      } else {
        assert.throws(() => installApp({ source, target, inspect, copy, backupRoot: root }));
        assert.equal(payload(target), "old");
      }
      assert.ok(existsSync(source));
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
}
