#!/usr/bin/env node
// 发版用的 DMG：拷签名公证过的 App + /Applications 链接 → hdiutil → 签名 → 公证 → staple → Gatekeeper → sha256。
// 以前这一步是每次发版手写的 shell 脚本；收进仓库，和 release:updater 一样跑在发版那台机器上。
//
// 坑（2026-10-25，v0.2.20）：hdiutil create 有时把刚做好的 DMG 留在挂载状态，diskimages-helper 持着文件锁，
// notarytool 打开它会一直卡在 open()——没有网络连接，Apple 那边也没有这次提交，看着像网络问题，等了 50 分钟。
// 所以做完立刻卸掉残留挂载，仍被占用就直接报错；公证也设了超时。
// 用法：node scripts/release-dmg.mjs     （公证 profile：VORTOCODE_NOTARY_PROFILE，默认 vortocode-notary）
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export function dmgAssetName(version) {
  if (!/^\d+\.\d+\.\d+$/.test(String(version))) throw new Error(`版本号不对：${version}`);
  return `VortoCode_${version}_aarch64.dmg`;
}

/** `hdiutil info` 里挂着这个映像的根设备（如 /dev/disk14）；没挂着返回 null。卸根设备会连带卸掉它派生的卷。 */
export function attachedDevice(hdiutilInfo, imagePath) {
  for (const block of String(hdiutilInfo).split(/^=+$/m)) {
    const path = block.match(/^image-path\s*:\s*(.+)$/m)?.[1]?.trim();
    if (path !== imagePath) continue;
    const device = block.match(/^(\/dev\/disk\d+)\s/m)?.[1];
    if (device) return device;
  }
  return null;
}

/** 从 `codesign -dvv` 的输出里取 Developer ID 身份；DMG 用和 App 同一个身份签。 */
export function developerIdAuthority(codesignDetails) {
  const authority = String(codesignDetails).match(/^Authority=(Developer ID Application:.*)$/m)?.[1];
  if (!authority) throw new Error("App 不是用 Developer ID Application 身份签的，不能做发版 DMG");
  return authority;
}

/** notarytool --output-format json 的结果；非 Accepted 一律当失败，带上提交号便于查日志。 */
export function notarizationOutcome(stdout) {
  let result = {};
  try {
    result = JSON.parse(String(stdout || "{}"));
  } catch {
    // 非 JSON（网络错误、超时）落到下面的失败分支。
  }
  if (result.status === "Accepted") return { ok: true, id: result.id };
  return { ok: false, id: result.id, status: result.status || "no result" };
}

function run(command, args, options = {}) {
  return execFileSync(command, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], ...options });
}

function detachIfAttached(dmg) {
  const device = attachedDevice(run("hdiutil", ["info"]), dmg);
  if (device) {
    console.log(`DMG 仍挂在 ${device}，先卸掉（否则公证会卡在打开文件这一步）`);
    run("hdiutil", ["detach", device]);
  }
  let holders = "";
  try {
    holders = run("lsof", [dmg]).trim();
  } catch {
    // lsof 找不到占用者时以非零退出：这正是要的结果。
  }
  if (holders) throw new Error(`DMG 仍被占用，不能提交公证：\n${holders}`);
}

function main() {
  const desktop = dirname(dirname(fileURLToPath(import.meta.url)));
  const version = JSON.parse(readFileSync(join(desktop, "src-tauri", "tauri.conf.json"), "utf8")).version;
  const bundle = join(desktop, "src-tauri", "target", "release", "bundle");
  const app = join(bundle, "macos", "VortoCode.app");
  const profile = process.env.VORTOCODE_NOTARY_PROFILE || "vortocode-notary";
  if (!existsSync(app)) throw new Error(`没有找到构建好的 App：${app}（先 npm run bundle:dev-signed）`);
  const built = run("plutil", ["-extract", "CFBundleShortVersionString", "raw", join(app, "Contents", "Info.plist")]).trim();
  if (built !== version) throw new Error(`App 是 ${built}，配置是 ${version}：先重新构建`);
  run("xcrun", ["stapler", "validate", app]);          // DMG 里装的 App 必须已经公证并 staple
  // codesign -dvv 把签名详情写在 stderr
  const details = spawnSync("codesign", ["-dvv", app], { encoding: "utf8" });
  const identity = developerIdAuthority(`${details.stdout}${details.stderr}`);

  const dmg = join(bundle, "dmg", dmgAssetName(version));
  mkdirSync(dirname(dmg), { recursive: true });
  rmSync(dmg, { force: true });
  const stage = mkdtempSync(join(tmpdir(), "vortocode-dmg-"));
  try {
    run("ditto", [app, join(stage, "VortoCode.app")]);
    symlinkSync("/Applications", join(stage, "Applications"));
    run("hdiutil", ["create", "-volname", "VortoCode", "-srcfolder", stage, "-format", "UDZO", "-ov", dmg]);
  } finally {
    rmSync(stage, { recursive: true, force: true });
  }
  detachIfAttached(dmg);

  run("codesign", ["--timestamp", "--sign", identity, dmg]);
  console.log(`提交公证（profile ${profile}），通常几分钟……`);
  let submitted = "";
  try {
    submitted = run("xcrun", ["notarytool", "submit", dmg, "--keychain-profile", profile,
      "--wait", "--timeout", "20m", "--output-format", "json"]);
  } catch (error) {
    submitted = error.stdout || "";
  }
  const outcome = notarizationOutcome(submitted);
  if (!outcome.ok) {
    const log = outcome.id ? run("xcrun", ["notarytool", "log", outcome.id, "--keychain-profile", profile]) : "";
    throw new Error([`DMG 公证没有通过：${outcome.status}${outcome.id ? `（提交 ${outcome.id}）` : ""}`,
      submitted.trim(), log.trim()].filter(Boolean).join("\n"));
  }
  console.log(`公证通过（提交 ${outcome.id}）`);
  run("xcrun", ["stapler", "staple", dmg]);
  run("xcrun", ["stapler", "validate", dmg]);
  run("spctl", ["--assess", "--type", "open", "--context", "context:primary-signature", dmg]);
  const digest = createHash("sha256").update(readFileSync(dmg)).digest("hex");
  console.log(`${dmg}\nsha256 ${digest}`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    main();
  } catch (error) {
    console.error(`生成发版 DMG 失败：${error instanceof Error ? error.message : error}`);
    process.exit(1);
  }
}
