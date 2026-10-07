import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { RUNTIME_DIR_NAME, runtimeExecutableName } from "./runtime-tree.mjs";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(desktopRoot, "..");

async function freePort() {
  const server = createServer();
  await new Promise((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListen);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise((resolveClose, reject) => server.close((error) => error ? reject(error) : resolveClose()));
  if (!port) throw new Error("failed to allocate a local sidecar smoke port");
  return port;
}

const runtime = process.env.VORTOCODE_RUNTIME_BIN || join(
  desktopRoot,
  "src-tauri",
  "binaries",
  RUNTIME_DIR_NAME,
  runtimeExecutableName(),
);
if (!existsSync(runtime)) throw new Error(`Desktop runtime sidecar not found: ${runtime}`);

const port = await freePort();
const startedAt = Date.now();
const runtimeEnv = { ...process.env };
delete runtimeEnv.VORTOCODE_API_TOKEN;
delete runtimeEnv.AUTODEV_API_TOKEN;
Object.assign(runtimeEnv, {
  VORTOCODE_CRON: "0",
  VORTOCODE_DESKTOP_SMOKE: "1",
  VORTOCODE_ENABLE_BROWSER: "0",
  VORTOCODE_ENABLE_SHELL: "0",
  VORTOCODE_HEARTBEAT: "0",
  VORTOCODE_IM: "",
});

const child = spawn(runtime, ["server", "--host", "127.0.0.1", "--port", String(port)], {
  cwd: repoRoot,
  env: runtimeEnv,
  stdio: ["ignore", "pipe", "pipe"],
});
let output = "";
const capture = (chunk) => {
  output = `${output}${chunk.toString()}`.slice(-40_000);
};
child.stdout.on("data", capture);
child.stderr.on("data", capture);
let exit = null;
child.once("exit", (code, signal) => {
  exit = { code, signal };
});

const sleep = (milliseconds) => new Promise((resolveSleep) => setTimeout(resolveSleep, milliseconds));
const deadline = Date.now() + Number(process.env.VORTOCODE_SIDECAR_SMOKE_TIMEOUT_MS || 90_000);
let health = null;
try {
  while (Date.now() < deadline && !exit) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/health/quick`, {
        signal: AbortSignal.timeout(2_000),
      });
      if (response.ok) {
        health = await response.json();
        break;
      }
    } catch {
      // First imports (and macOS's first scan of freshly installed binaries) are included in the timeout.
    }
    await sleep(250);
  }
  if (!health || health.status !== "healthy") {
    throw new Error(`sidecar did not become healthy; exit=${JSON.stringify(exit)}\n${output}`);
  }
  const extensionsResponse = await fetch(`http://127.0.0.1:${port}/api/extensions/inspect`, {
    signal: AbortSignal.timeout(5_000),
  });
  const extensions = await extensionsResponse.json();
  if (
    !extensionsResponse.ok
    || extensions.version !== 1
    || !Array.isArray(extensions.items)
    || !Array.isArray(extensions.issues)
    || typeof extensions.summary?.total !== "number"
  ) {
    throw new Error(`sidecar extension inventory failed (${extensionsResponse.status}): ${JSON.stringify(extensions)}`);
  }
  const page = await fetch(`http://127.0.0.1:${port}/agent`, { signal: AbortSignal.timeout(5_000) });
  const html = await page.text();
  if (!page.ok || !html.includes("VortoCode · 主 Agent")) {
    throw new Error(`sidecar did not serve bundled Web assets (${page.status})`);
  }
  const terminalResponse = await fetch(`http://127.0.0.1:${port}/api/terminals`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ cols: 90, rows: 24 }),
    signal: AbortSignal.timeout(5_000),
  });
  if (terminalResponse.status !== 403) {
    throw new Error(`sidecar allowed host terminal while shell is disabled (${terminalResponse.status})`);
  }
  console.log(`Desktop runtime sidecar smoke passed in ${Date.now() - startedAt} ms (${runtime})`);
} finally {
  if (!exit) child.kill("SIGTERM");
  const stopDeadline = Date.now() + 5_000;
  while (!exit && Date.now() < stopDeadline) await sleep(100);
  if (!exit) child.kill("SIGKILL");
}
