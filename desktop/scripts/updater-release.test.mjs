import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { GITHUB_LATEST, MIRROR_BASE, MIRROR_LATEST, PLATFORM, REPO, UPDATE_ENDPOINTS, mirrorAssetBase, updaterAssetName,
  updaterManifest }
  from "./updater-release.mjs";

const desktop = dirname(dirname(fileURLToPath(import.meta.url)));

test("latest.json points at this release's archive on this repo", () => {
  const manifest = updaterManifest({ version: "0.2.14", notes: "  修了几处 \n", pubDate: "2026-10-22T08:00:00.000Z",
    signature: "dW50cnVzdGVk\n" });
  assert.deepEqual(manifest, {
    version: "0.2.14", notes: "修了几处", pub_date: "2026-10-22T08:00:00.000Z",
    mirrors: [`${MIRROR_BASE}/v0.2.14/${updaterAssetName("0.2.14")}`,
      `https://github.com/${REPO}/releases/download/v0.2.14/${updaterAssetName("0.2.14")}`],
    platforms: { [PLATFORM]: { signature: "dW50cnVzdGVk",
      url: `https://github.com/${REPO}/releases/download/v0.2.14/${updaterAssetName("0.2.14")}` } },
  });
});

test("the mirror manifest is the same release but downloads from OSS", () => {
  const fields = { version: "0.2.56", notes: "x", pubDate: "2026-11-09T08:00:00.000Z", signature: "c2ln" };
  const github = updaterManifest(fields);
  const mirror = updaterManifest({ ...fields, base: mirrorAssetBase("0.2.56") });
  // 每一版一个目录，清单固定在根上
  assert.equal(mirror.platforms[PLATFORM].url, `${MIRROR_BASE}/v0.2.56/${updaterAssetName("0.2.56")}`);
  assert.equal(MIRROR_LATEST, `${MIRROR_BASE}/latest.json`);
  assert.equal(mirror.platforms[PLATFORM].signature, github.platforms[PLATFORM].signature);
  assert.deepEqual({ ...mirror, platforms: null }, { ...github, platforms: null });
  // 两份清单都列出两个来源，App 自己挑快的
  assert.deepEqual(mirror.mirrors, [mirror.platforms[PLATFORM].url, github.platforms[PLATFORM].url]);
});

test("refuses a manifest that the updater could not verify", () => {
  const ok = { version: "0.2.14", pubDate: "2026-10-22T08:00:00Z", signature: "x" };
  assert.throws(() => updaterManifest({ ...ok, signature: " " }), /签名是空的/);
  assert.throws(() => updaterManifest({ ...ok, version: "v0.2.14" }), /版本号不对/);
  assert.throws(() => updaterManifest({ ...ok, pubDate: "昨天" }), /发布时间不对/);
});

test("the app checks the OSS mirror first, then this repo's latest release, with a pinned public key", () => {
  const config = JSON.parse(readFileSync(join(desktop, "src-tauri", "tauri.conf.json"), "utf8"));
  const updater = config.plugins.updater;
  assert.deepEqual(updater.endpoints, UPDATE_ENDPOINTS);
  assert.deepEqual(UPDATE_ENDPOINTS, [MIRROR_LATEST, GITHUB_LATEST]);
  assert.equal(GITHUB_LATEST, `https://github.com/${REPO}/releases/latest/download/latest.json`);
  assert.ok(UPDATE_ENDPOINTS.every((url) => url.startsWith("https://")));
  assert.match(Buffer.from(updater.pubkey, "base64").toString("utf8"), /^untrusted comment: minisign public key/);
});
