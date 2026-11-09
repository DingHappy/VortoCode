import { test } from "node:test";
import assert from "node:assert/strict";
import { BUCKET, PREFIX, releaseUploads, stringToSign } from "./oss-release.mjs";
import { MIRROR_BASE } from "./updater-release.mjs";

test("uploads go to the bucket and folder the app downloads from", () => {
  assert.equal(`https://${BUCKET}.oss-cn-hangzhou.aliyuncs.com/${PREFIX}`, MIRROR_BASE);
  assert.equal(BUCKET, "vorto");
  assert.equal(PREFIX, "vortoone/desktop");
});

test("the V1 string to sign covers verb, body hash, type, date and the object", () => {
  assert.equal(
    stringToSign({ verb: "PUT", md5: "bWQ1", type: "application/json", date: "Mon, 09 Nov 2026 08:00:00 GMT",
      key: "vortoone/desktop/latest.json" }),
    "PUT\nbWQ1\napplication/json\nMon, 09 Nov 2026 08:00:00 GMT\n/vorto/vortoone/desktop/latest.json",
  );
});

test("each release goes into its own v<version>/ folder: update archive and DMG", () => {
  assert.deepEqual(releaseUploads("0.2.56", "/b").map(([local, key]) => [local, key]), [
    ["/b/updater/VortoOne_0.2.56_aarch64.app.tar.gz", "vortoone/desktop/v0.2.56/VortoOne_0.2.56_aarch64.app.tar.gz"],
    ["/b/dmg/VortoOne_0.2.56_aarch64.dmg", "vortoone/desktop/v0.2.56/VortoOne_0.2.56_aarch64.dmg"],
  ]);
});
