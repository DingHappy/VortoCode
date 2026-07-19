import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const releaseRoot = join(desktopRoot, "src-tauri", "target", "release");
const marker = join(tmpdir(), `vortocode-desktop-smoke-${process.pid}.json`);

const defaultExecutable = process.platform === "darwin"
  ? join(releaseRoot, "bundle", "macos", "VortoCode.app", "Contents", "MacOS", "vortocode-desktop")
  : process.platform === "win32"
    ? join(releaseRoot, "vortocode-desktop.exe")
    : join(releaseRoot, "vortocode-desktop");
const executable = process.env.VORTOCODE_DESKTOP_SMOKE_EXECUTABLE
  ? resolve(process.env.VORTOCODE_DESKTOP_SMOKE_EXECUTABLE)
  : defaultExecutable;

if (!existsSync(executable)) {
  throw new Error(`找不到 Desktop release bundle：${executable}；请先运行 npm run bundle:app`);
}

try {
  unlinkSync(marker);
} catch (error) {
  if (error?.code !== "ENOENT") throw error;
}

const launched = spawnSync(executable, [], {
  cwd: desktopRoot,
  env: { ...process.env, VORTOCODE_DESKTOP_SMOKE_FILE: marker },
  encoding: "utf8",
  timeout: 60_000,
});

if (launched.error) {
  throw launched.error;
}
if (launched.status !== 0) {
  throw new Error([
    `Desktop bundle 退出码异常：${launched.status ?? launched.signal ?? "unknown"}`,
    launched.stdout?.trim(),
    launched.stderr?.trim(),
  ].filter(Boolean).join("\n"));
}
if (!existsSync(marker)) {
  throw new Error("Desktop bundle 已退出，但没有收到 React 挂载后的 Tauri IPC smoke 标记");
}

const result = JSON.parse(readFileSync(marker, "utf8"));
unlinkSync(marker);
if (
  result.uiMounted !== true
  || result.sidecarReady !== true
  || !String(result.sidecarVersion || "").startsWith("vortocode-runtime ")
  || !Number.isInteger(result.pid)
  || result.pid <= 0
) {
  throw new Error(`Desktop GUI smoke 标记无效：${JSON.stringify(result)}`);
}

console.log(`Desktop GUI smoke passed (pid ${result.pid}, React mounted, Tauri IPC ready, ${result.sidecarVersion})`);
