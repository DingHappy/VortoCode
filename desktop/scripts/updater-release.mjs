#!/usr/bin/env node
// 发版时生成一键更新要的三样东西，跟 DMG 一起传到同一个 GitHub Release：
//   VortoCode_<版本>_aarch64.app.tar.gz   签名公证并 staple 过的 .app 打包
//   VortoCode_<版本>_aarch64.app.tar.gz.sig
//   latest.json                            Desktop 按 tauri.conf.json 里的地址来取，验签用内置公钥
// 签名私钥只在发版的这台机器上：VORTOCODE_UPDATER_KEY（默认 ~/.vortocode/updater.key），从不进仓库。
// 用法：node scripts/updater-release.mjs [更新说明文件]
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO = "DingHappy/VortoCode";
export const PLATFORM = "darwin-aarch64";

export function updaterAssetName(version) {
  return `VortoCode_${version}_aarch64.app.tar.gz`;
}

/** latest.json：只有一个平台；下载地址固定指向本仓库这一版的 Release 附件。 */
export function updaterManifest({ version, notes = "", pubDate, signature }) {
  if (!/^\d+\.\d+\.\d+$/.test(String(version))) throw new Error(`版本号不对：${version}`);
  if (!String(signature || "").trim()) throw new Error("更新包签名是空的");
  if (Number.isNaN(Date.parse(pubDate))) throw new Error(`发布时间不对：${pubDate}`);
  return {
    version,
    notes: String(notes).trim(),
    pub_date: pubDate,
    platforms: {
      [PLATFORM]: {
        signature: String(signature).trim(),
        url: `https://github.com/${REPO}/releases/download/v${version}/${updaterAssetName(version)}`,
      },
    },
  };
}

function main() {
  const desktop = dirname(dirname(fileURLToPath(import.meta.url)));
  const version = JSON.parse(readFileSync(join(desktop, "src-tauri", "tauri.conf.json"), "utf8")).version;
  const bundle = join(desktop, "src-tauri", "target", "release", "bundle");
  const app = join(bundle, "macos", "VortoCode.app");
  const key = process.env.VORTOCODE_UPDATER_KEY || join(homedir(), ".vortocode", "updater.key");
  if (!existsSync(app)) throw new Error(`没有找到构建好的 App：${app}`);
  if (!existsSync(key)) throw new Error(`没有找到更新签名私钥：${key}`);
  const built = execFileSync("plutil", ["-extract", "CFBundleShortVersionString", "raw", join(app, "Contents", "Info.plist")],
    { encoding: "utf8" }).trim();
  if (built !== version) throw new Error(`App 是 ${built}，配置是 ${version}：先重新构建`);
  // 必须是公证并 staple 过的：更新后的 App 要能直接过 Gatekeeper
  execFileSync("xcrun", ["stapler", "validate", app], { stdio: "ignore" });

  const out = join(bundle, "updater");
  mkdirSync(out, { recursive: true });
  const archive = join(out, updaterAssetName(version));
  rmSync(archive, { force: true });
  rmSync(`${archive}.sig`, { force: true });
  execFileSync("tar", ["-czf", archive, "-C", join(bundle, "macos"), "VortoCode.app"],
    { env: { ...process.env, COPYFILE_DISABLE: "1" } });     // 不带 macOS 的 ._ 附属文件
  execFileSync("npx", ["tauri", "signer", "sign", "--private-key-path", key, "--password", "", archive],
    { cwd: desktop, stdio: ["ignore", "ignore", "inherit"] });
  const notesFile = process.argv[2];
  const manifest = updaterManifest({
    version,
    notes: notesFile ? readFileSync(notesFile, "utf8") : "",
    pubDate: new Date().toISOString(),
    signature: readFileSync(`${archive}.sig`, "utf8"),
  });
  const latest = join(out, "latest.json");
  writeFileSync(latest, `${JSON.stringify(manifest, null, 2)}\n`);
  console.log([archive, `${archive}.sig`, latest].join("\n"));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    main();
  } catch (error) {
    console.error(`生成更新包失败：${error instanceof Error ? error.message : error}`);
    process.exit(1);
  }
}
