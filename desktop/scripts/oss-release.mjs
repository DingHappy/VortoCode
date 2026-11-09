#!/usr/bin/env node
// 把这一版的更新包、DMG 和镜像用的 latest.json 传到阿里云 OSS，再从公网取回核对。在 release:dmg、release:updater 之后运行。
//   目录：vortoone/desktop/v<版本>/ 放更新包和 DMG，vortoone/desktop/latest.json 是 App 固定来问的清单。
//   先传包、核对公网取回的 sha256，最后才传 latest.json——任何时刻镜像上的清单都不会指向一个还没传完的包。
// 上传凭据只在发版的这台机器上：macOS 钥匙串条目 vortoone-oss-upload（账户名 = AccessKey ID，密码 = Secret），
// 属于只能读写 bucket `vorto` 下 vortoone/desktop/ 的 RAM 子账号。镜像被人改了也装不进去：App 只认内置公钥签过的包。
// 用法：node scripts/oss-release.mjs
import { execFileSync } from "node:child_process";
import { createHash, createHmac } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { MIRROR_BASE, dmgAssetName, mirrorAssetBase, updaterAssetName } from "./updater-release.mjs";

export const KEYCHAIN_SERVICE = "vortoone-oss-upload";
const mirror = new URL(MIRROR_BASE);
export const BUCKET = mirror.hostname.split(".")[0];
export const PREFIX = mirror.pathname.replace(/^\/+|\/+$/g, "");

/** OSS V1 签名的待签字符串（不带 x-oss-* 头）：动词、Content-MD5、Content-Type、Date、/bucket/对象。 */
export function stringToSign({ verb, md5 = "", type = "", date, key }) {
  return `${verb}\n${md5}\n${type}\n${date}\n/${BUCKET}/${key}`;
}

function credentials() {
  const read = (args) => execFileSync("security", ["find-generic-password", "-s", KEYCHAIN_SERVICE, ...args],
    { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  let attrs, secret;
  try {
    attrs = read([]);
    secret = read(["-w"]).trim();
  } catch {
    throw new Error(`钥匙串里没有 ${KEYCHAIN_SERVICE}：在「终端」里运行 `
      + `security add-generic-password -s ${KEYCHAIN_SERVICE} -a <AccessKeyId> -w`);
  }
  const id = /"acct"<blob>="([^"]+)"/.exec(attrs)?.[1];
  if (!id || !secret) throw new Error(`钥匙串条目 ${KEYCHAIN_SERVICE} 缺 AccessKey ID 或 Secret`);
  return { id, secret };
}

async function put({ id, secret }, key, body, type, cacheControl) {
  const md5 = createHash("md5").update(body).digest("base64");
  const date = new Date().toUTCString();
  const signature = createHmac("sha1", secret).update(stringToSign({ verb: "PUT", md5, type, date, key })).digest("base64");
  const headers = { "Content-MD5": md5, "Content-Type": type, Date: date, Authorization: `OSS ${id}:${signature}` };
  if (cacheControl) headers["Cache-Control"] = cacheControl;
  const res = await fetch(`https://${mirror.host}/${key}`, { method: "PUT", body, headers });
  if (!res.ok) {
    const code = /<Code>([^<]*)/.exec(await res.text())?.[1] || "";
    throw new Error(`上传 ${key} 失败：HTTP ${res.status} ${code}`);
  }
}

async function publicBytes(url) {
  const res = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`公网取不回 ${url}：HTTP ${res.status}（bucket 要是公共读）`);
  return Buffer.from(await res.arrayBuffer());
}

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

/** 这一版要传的包：[本地路径, 镜像上的对象路径, Content-Type]。 */
export function releaseUploads(version, bundle) {
  const folder = `${PREFIX}/v${version}`;
  return [
    [join(bundle, "updater", updaterAssetName(version)), `${folder}/${updaterAssetName(version)}`, "application/gzip"],
    [join(bundle, "dmg", dmgAssetName(version)), `${folder}/${dmgAssetName(version)}`, "application/x-apple-diskimage"],
  ];
}

async function main() {
  const desktop = dirname(dirname(fileURLToPath(import.meta.url)));
  const version = JSON.parse(readFileSync(join(desktop, "src-tauri", "tauri.conf.json"), "utf8")).version;
  const bundle = join(desktop, "src-tauri", "target", "release", "bundle");
  const uploads = releaseUploads(version, bundle);
  const manifestPath = join(bundle, "updater", "oss", "latest.json");
  for (const path of [...uploads.map(([local]) => local), manifestPath]) {
    if (!existsSync(path)) throw new Error(`没有 ${path}：先运行 npm run release:dmg 和 release:updater`);
  }
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const assetUrl = `${mirrorAssetBase(version)}/${updaterAssetName(version)}`;
  if (manifest.version !== version || Object.values(manifest.platforms || {})[0]?.url !== assetUrl) {
    throw new Error(`${manifestPath} 不是 ${version} 的镜像清单：重新运行 npm run release:updater`);
  }
  const creds = credentials();

  for (const [local, key, type] of uploads) {
    const bytes = readFileSync(local);
    const url = `${mirror.origin}/${key}`;
    await put(creds, key, bytes, type);
    if (sha256(await publicBytes(url)) !== sha256(bytes)) throw new Error(`公网取回的 ${url} 与本地不一致`);
    console.log(`✓ ${url}（${(bytes.length / 1048576).toFixed(1)} MB，sha256 一致）`);
  }

  // 清单要立刻对所有人生效：不让中间缓存留旧版本
  await put(creds, `${PREFIX}/latest.json`, readFileSync(manifestPath), "application/json", "no-cache");
  const live = JSON.parse((await publicBytes(`${MIRROR_BASE}/latest.json`)).toString("utf8"));
  if (live.version !== version) throw new Error(`镜像 latest.json 还是 ${live.version}`);
  console.log(`✓ 清单 ${MIRROR_BASE}/latest.json → ${live.version}`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => {
    console.error(`上传 OSS 镜像失败：${error instanceof Error ? error.message : error}`);
    process.exit(1);
  });
}
