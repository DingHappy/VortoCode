import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(desktopRoot, "..");

function commandOutput(command, args) {
  const result = spawnSync(command, args, { cwd: repoRoot, encoding: "utf8" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr || result.stdout || `${command} failed`);
  return result.stdout.trim();
}

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

const targetTriple = commandOutput("rustc", ["--print", "host-tuple"]);
const extension = process.platform === "win32" ? ".exe" : "";
const runtime = process.env.VORTOCODE_RUNTIME_BIN || join(
  desktopRoot,
  "src-tauri",
  "binaries",
  `vortocode-runtime-${targetTriple}${extension}`,
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
      // PyInstaller one-file extraction and first imports are intentionally included in the timeout.
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
  if (process.platform !== "win32") {
    const terminalResponse = await fetch(`http://127.0.0.1:${port}/api/terminals`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ cols: 90, rows: 24 }),
      signal: AbortSignal.timeout(5_000),
    });
    const terminal = await terminalResponse.json();
    if (!terminalResponse.ok || !terminal.id) {
      throw new Error(`sidecar PTY creation failed (${terminalResponse.status}): ${JSON.stringify(terminal)}`);
    }
    const marker = "__VORTOCODE_PACKAGED_PTY_OK__";
    const inputResponse = await fetch(
      `http://127.0.0.1:${port}/api/terminals/${encodeURIComponent(terminal.id)}/input`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ data: `printf '${marker}\\n'\n` }),
        signal: AbortSignal.timeout(5_000),
      },
    );
    if (!inputResponse.ok) {
      throw new Error(`sidecar PTY input failed (${inputResponse.status}): ${await inputResponse.text()}`);
    }
    let terminalOutput = "";
    let offset = Number(terminal.offset || 0);
    while (Date.now() < deadline && !terminalOutput.includes(marker)) {
      const readResponse = await fetch(
        `http://127.0.0.1:${port}/api/terminals/${encodeURIComponent(terminal.id)}/output?offset=${offset}`,
        { signal: AbortSignal.timeout(5_000) },
      );
      const snapshot = await readResponse.json();
      if (!readResponse.ok) {
        throw new Error(`sidecar PTY output failed (${readResponse.status}): ${JSON.stringify(snapshot)}`);
      }
      terminalOutput += String(snapshot.output || "");
      offset = Number(snapshot.offset || offset);
      if (!terminalOutput.includes(marker)) await sleep(50);
    }
    await fetch(
      `http://127.0.0.1:${port}/api/terminals/${encodeURIComponent(terminal.id)}/stop`,
      { method: "POST", signal: AbortSignal.timeout(5_000) },
    );
    if (!terminalOutput.includes(marker)) {
      throw new Error(`sidecar packaged PTY produced no marker; output=${terminalOutput.slice(-2_000)}`);
    }
  }
  console.log(`Desktop runtime sidecar smoke passed in ${Date.now() - startedAt} ms (${runtime})`);
} finally {
  if (!exit) child.kill("SIGTERM");
  const stopDeadline = Date.now() + 5_000;
  while (!exit && Date.now() < stopDeadline) await sleep(100);
  if (!exit) child.kill("SIGKILL");
}
