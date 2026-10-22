import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { PLATFORM, REPO, updaterAssetName, updaterManifest } from "./updater-release.mjs";

const desktop = dirname(dirname(fileURLToPath(import.meta.url)));

test("latest.json points at this release's archive on this repo", () => {
  const manifest = updaterManifest({ version: "0.2.14", notes: "  修了几处 \n", pubDate: "2026-10-22T08:00:00.000Z",
    signature: "dW50cnVzdGVk\n" });
  assert.deepEqual(manifest, {
    version: "0.2.14", notes: "修了几处", pub_date: "2026-10-22T08:00:00.000Z",
    platforms: { [PLATFORM]: { signature: "dW50cnVzdGVk",
      url: `https://github.com/${REPO}/releases/download/v0.2.14/${updaterAssetName("0.2.14")}` } },
  });
});

test("refuses a manifest that the updater could not verify", () => {
  const ok = { version: "0.2.14", pubDate: "2026-10-22T08:00:00Z", signature: "x" };
  assert.throws(() => updaterManifest({ ...ok, signature: " " }), /签名是空的/);
  assert.throws(() => updaterManifest({ ...ok, version: "v0.2.14" }), /版本号不对/);
  assert.throws(() => updaterManifest({ ...ok, pubDate: "昨天" }), /发布时间不对/);
});

test("the app checks this repo's latest release with a pinned public key", () => {
  const config = JSON.parse(readFileSync(join(desktop, "src-tauri", "tauri.conf.json"), "utf8"));
  const updater = config.plugins.updater;
  assert.deepEqual(updater.endpoints, [`https://github.com/${REPO}/releases/latest/download/latest.json`]);
  assert.match(Buffer.from(updater.pubkey, "base64").toString("utf8"), /^untrusted comment: minisign public key/);
});
