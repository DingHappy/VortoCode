#!/usr/bin/env node
// 装好新版之后的真机检查：只读，不另起 App、不点界面，查的是你此刻正在用的那个 App。
//   版本对不对 · 签名 / 公证 / Gatekeeper · App 和内置引擎在不在跑 · 引擎接口通不通 · 有没有残留的旧引擎 · 更新通道是不是指向这一版
// 看界面才能确认的几项，最后列成一份一分钟能点完的清单。
// 用法：npm run check:installed [-- 期望版本]（默认取 tauri.conf.json 的版本）
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const APP = "/Applications/VortoCode.app";
export const APP_BINARY = `${APP}/Contents/MacOS/vortocode-desktop`;
export const RUNTIME_BINARY = `${APP}/Contents/Resources/runtime/vortocode-runtime`;
export const LATEST_URL = "https://github.com/DingHappy/VortoCode/releases/latest/download/latest.json";

export const MANUAL_CHECKS = [
  "侧栏能看到「最近」和「项目」，点一条旧会话能打开",
  "新开一个对话，发一句话，能收到回复（没接模型时输入框右下角显示「未连接模型」）",
  "切到助理模式，右侧面板的「动态 / 定时 / 身份」三个标签都能打开",
  "设置 → 高级 →「更新」里显示的版本号就是这次装的版本",
];

/** 从 `ps -axo pid=,command=` 的输出里挑出这个 App 和它的内置引擎。 */
export function parseProcesses(text) {
  const app = [];
  const runtimes = [];
  const strays = [];
  for (const raw of String(text).split("\n")) {
    const line = raw.trim();
    const match = line.match(/^(\d+)\s+(.*)$/);
    if (!match) continue;
    const [, pid, command] = match;
    if (command === APP_BINARY || command.startsWith(`${APP_BINARY} `)) app.push(Number(pid));
    else if (command.startsWith(`${RUNTIME_BINARY} server`)) {
      const port = command.match(/--port (\d+)/);
      runtimes.push({ pid: Number(pid), port: port ? Number(port[1]) : null });
    } else if (/\/vortocode-runtime server\b/.test(command)) strays.push({ pid: Number(pid), command: command.slice(0, 160) });
  }
  return { app, runtimes, strays };
}

/** 把一组检查结果排成给人看的几行；任何一项没过就算失败。 */
export function summarize(results) {
  const lines = results.map((item) => `${item.ok ? "✅" : item.warn ? "⚠️ " : "❌"} ${item.name}：${item.detail}`);
  return { ok: results.every((item) => item.ok || item.warn), text: lines.join("\n") };
}

/** 跑一条命令，stdout 和 stderr 都收（spctl、stapler 的结论写在 stderr 里）。 */
function run(command, args) {
  const result = spawnSync(command, args, { encoding: "utf8" });
  const out = `${result.stdout || ""}${result.stderr || ""}`.trim() || String(result.error?.message || "");
  return { ok: result.status === 0, out };
}

async function getJson(url, timeoutMs = 8000) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function main() {
  const desktop = dirname(dirname(fileURLToPath(import.meta.url)));
  const expected = process.argv[2]
    || JSON.parse(readFileSync(join(desktop, "src-tauri", "tauri.conf.json"), "utf8")).version;
  const results = [];
  const add = (name, ok, detail, warn = false) => results.push({ name, ok, detail, warn });

  const version = run("plutil", ["-extract", "CFBundleShortVersionString", "raw", `${APP}/Contents/Info.plist`]);
  add("版本", version.ok && version.out === expected, version.ok ? `装的是 ${version.out}，期望 ${expected}` : "读不到 Info.plist");
  const codesign = run("codesign", ["--verify", "--deep", "--strict", APP]);
  add("签名", codesign.ok, codesign.ok ? "完整" : codesign.out.split("\n")[0]);
  const gatekeeper = run("spctl", ["-a", "-vv", APP]);
  add("Gatekeeper", gatekeeper.ok && /Notarized Developer ID/.test(gatekeeper.out),
    gatekeeper.ok ? "通过（已公证）" : gatekeeper.out.split("\n")[0]);
  const staple = run("xcrun", ["stapler", "validate", APP]);
  add("公证票据", staple.ok, staple.ok ? "已钉在 App 里" : "没有钉上票据");

  const { app, runtimes, strays } = parseProcesses(run("ps", ["-axo", "pid=,command="]).out);
  add("App 进程", app.length === 1, app.length ? `pid ${app.join(", ")}` : "没在运行：先打开 App 再查");
  add("内置引擎", runtimes.length >= 1, runtimes.length ? runtimes.map((item) => `pid ${item.pid} 端口 ${item.port}`).join("；") : "没有在跑");
  for (const runtime of runtimes.filter((item) => item.port)) {
    try {
      const health = await getJson(`http://127.0.0.1:${runtime.port}/api/health/quick`, 5000);
      add(`引擎接口 :${runtime.port}`, health?.status === "healthy", `status=${health?.status}`);
    } catch (error) {
      add(`引擎接口 :${runtime.port}`, false, `连不上（${error.message}）`);
    }
  }
  add("残留的旧引擎", strays.length === 0,
    strays.length ? `有 ${strays.length} 个不是这个 App 启动的引擎：${strays.map((item) => item.pid).join(", ")}` : "没有", strays.length > 0);

  try {
    const recovery = JSON.parse(readFileSync(join(homedir(), "Library/Application Support/com.vorto.vortocode/gateway-recovery.json"), "utf8"));
    const general = (recovery.runtimes || []).find((item) => item.runtimeId === "general");
    const alive = general && runtimes.some((item) => item.pid === general.pid);
    add("恢复记录", Boolean(alive), general ? `通用会话 pid ${general.pid}${alive ? "，和正在跑的一致" : "，但这个进程不在了"}` : "没有通用会话的记录");
  } catch {
    add("恢复记录", false, "读不到 gateway-recovery.json", true);
  }

  try {
    const latest = await getJson(LATEST_URL);
    add("更新通道", latest.version === expected, `GitHub 最新是 ${latest.version}`, latest.version !== expected);
  } catch (error) {
    add("更新通道", false, `取不到 latest.json（${error.message}）`, true);
  }

  const summary = summarize(results);
  console.log(summary.text);
  console.log(`\n还要你在 App 里看一眼（一分钟）：\n${MANUAL_CHECKS.map((item, index) => `  ${index + 1}. ${item}`).join("\n")}`);
  process.exit(summary.ok ? 0 : 1);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => {
    console.error(`检查没跑完：${error instanceof Error ? error.message : error}`);
    process.exit(1);
  });
}
