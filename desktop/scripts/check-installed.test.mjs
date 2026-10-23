import { test } from "node:test";
import assert from "node:assert/strict";
import { APP_BINARY, MANUAL_CHECKS, RUNTIME_BINARY, parseProcesses, summarize } from "./check-installed.mjs";

test("finds the installed app, its runtimes with ports, and stray runtimes from elsewhere", () => {
  const ps = [
    `  27248 ${APP_BINARY}`,
    `  27274 ${RUNTIME_BINARY} server --host 127.0.0.1 --port 59326`,
    `  31000 ${RUNTIME_BINARY} server --host 127.0.0.1 --port 49703`,
    "  4100 /private/var/folders/x/AppTranslocation/VortoCode.app/Contents/Resources/runtime/vortocode-runtime server --port 50000",
    "  4200 /usr/bin/python3 -m src.cli server --port 8080",
    "  4300 /Applications/VortoCode.app/Contents/Resources/runtime/vortocode-runtime --version",
    "garbage line",
  ].join("\n");
  const found = parseProcesses(ps);
  assert.deepEqual(found.app, [27248]);
  assert.deepEqual(found.runtimes, [{ pid: 27274, port: 59326 }, { pid: 31000, port: 49703 }]);
  assert.equal(found.strays.length, 1);
  assert.equal(found.strays[0].pid, 4100);
});

test("warnings do not fail the check, failures do", () => {
  const passing = summarize([{ name: "版本", ok: true, detail: "0.2.15" }, { name: "更新通道", ok: false, warn: true, detail: "落后" }]);
  assert.equal(passing.ok, true);
  assert.match(passing.text, /✅ 版本：0.2.15\n⚠️  更新通道：落后/);
  const failing = summarize([{ name: "签名", ok: false, detail: "被改过" }]);
  assert.equal(failing.ok, false);
  assert.equal(failing.text, "❌ 签名：被改过");
});

test("manual checklist stays short", () => {
  assert.ok(MANUAL_CHECKS.length <= 5);
});
