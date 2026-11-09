#!/usr/bin/env node
// 发版时生成一键更新要的东西，跟 DMG 一起传到同一个 GitHub Release：
//   VortoOne_<版本>_aarch64.app.tar.gz   签名公证并 staple 过的 .app 打包
//   VortoOne_<版本>_aarch64.app.tar.gz.sig
//   latest.json                            下载地址指向 GitHub Release
//   oss/latest.json                        同一份清单，下载地址指向阿里云 OSS 镜像（npm run release:oss 上传）
// Desktop 先取 OSS 上的 latest.json，取不到再取 GitHub 的；两边的更新包都要用内置公钥验签才装。
// 签名私钥只在发版的这台机器上：VORTOONE_UPDATER_KEY（默认 ~/.vortoone/updater.key），从不进仓库。
// 用法：node scripts/updater-release.mjs [更新说明文件]
import "./env-names.mjs";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO = "DingHappy/VortoOne";
export const PLATFORM = "darwin-aarch64";
/** 国内镜像：阿里云 OSS 公共读 bucket `vorto` 的 vortoone/desktop/ 目录。从国内连 GitHub 下载更新包只有几十 KB/s。 */
export const MIRROR_BASE = "https://vorto.oss-cn-hangzhou.aliyuncs.com/vortoone/desktop";
export const GITHUB_LATEST = `https://github.com/${REPO}/releases/latest/download/latest.json`;
export const MIRROR_LATEST = `${MIRROR_BASE}/latest.json`;
/** App 检查更新的顺序：先镜像，取不到（网络错误或非 2xx）再 GitHub。 */
export const UPDATE_ENDPOINTS = [MIRROR_LATEST, GITHUB_LATEST];

export function githubAssetBase(version) {
  return `https://github.com/${REPO}/releases/download/v${version}`;
}

/** 镜像上每一版一个目录（v<版本>/，和 git tag 同名），放更新包和 DMG；latest.json 固定在根上，App 只认这个地址。 */
export function mirrorAssetBase(version) {
  return `${MIRROR_BASE}/v${version}`;
}

export function dmgAssetName(version) {
  return `VortoOne_${version}_aarch64.dmg`;
}

export function updaterAssetName(version) {
  return `VortoOne_${version}_aarch64.app.tar.gz`;
}

/** latest.json：只有一个平台；下载地址默认指向本仓库这一版的 Release 附件，镜像那份传 base: mirrorAssetBase(version)。 */
export function updaterManifest({ version, notes = "", pubDate, signature, base }) {
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
        url: `${base || githubAssetBase(version)}/${updaterAssetName(version)}`,
      },
    },
  };
}

function main() {
  const desktop = dirname(dirname(fileURLToPath(import.meta.url)));
  const version = JSON.parse(readFileSync(join(desktop, "src-tauri", "tauri.conf.json"), "utf8")).version;
  const bundle = join(desktop, "src-tauri", "target", "release", "bundle");
  const app = join(bundle, "macos", "VortoOne.app");
  // 状态目录改名前（ADR-018 批次 C）私钥在 ~/.vortocode；还没搬的机器上读旧位置
  const key = process.env.VORTOONE_UPDATER_KEY
    || [".vortoone", ".vortocode"].map((dir) => join(homedir(), dir, "updater.key")).find((path) => existsSync(path))
    || join(homedir(), ".vortoone", "updater.key");
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
  execFileSync("tar", ["-czf", archive, "-C", join(bundle, "macos"), "VortoOne.app"],
    { env: { ...process.env, COPYFILE_DISABLE: "1" } });     // 不带 macOS 的 ._ 附属文件
  execFileSync("npx", ["tauri", "signer", "sign", "--private-key-path", key, "--password", "", archive],
    { cwd: desktop, stdio: ["ignore", "ignore", "inherit"] });
  const notesFile = process.argv[2];
  const fields = {
    version,
    notes: notesFile ? readFileSync(notesFile, "utf8") : "",
    pubDate: new Date().toISOString(),
    signature: readFileSync(`${archive}.sig`, "utf8"),
  };
  const latest = join(out, "latest.json");
  writeFileSync(latest, `${JSON.stringify(updaterManifest(fields), null, 2)}\n`);
  const mirrorLatest = join(out, "oss", "latest.json");
  mkdirSync(dirname(mirrorLatest), { recursive: true });
  writeFileSync(mirrorLatest, `${JSON.stringify(updaterManifest({ ...fields, base: mirrorAssetBase(version) }), null, 2)}\n`);
  console.log([archive, `${archive}.sig`, latest, mirrorLatest].join("\n"));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    main();
  } catch (error) {
    console.error(`生成更新包失败：${error instanceof Error ? error.message : error}`);
    process.exit(1);
  }
}
