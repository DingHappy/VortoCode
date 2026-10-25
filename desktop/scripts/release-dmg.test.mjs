import { test } from "node:test";
import assert from "node:assert/strict";

import { attachedDevice, developerIdAuthority, dmgAssetName, notarizationOutcome } from "./release-dmg.mjs";

const DMG = "/repo/desktop/src-tauri/target/release/bundle/dmg/VortoCode_0.2.20_aarch64.dmg";

// 形状取自真机 `hdiutil info`：块之间用等号行分隔，一个映像会派生出好几个 /dev/disk。
const info = `framework       : 683.160.3
images          : 2
================================================
image-path      : /Users/someone/Downloads/Other.dmg
image-type      : read-only disk image
/dev/disk6\tGUID_partition_scheme\t
/dev/disk6s1\t7C3457EF-0000-11AA-AA11-00306543ECAC\t
================================================
image-path      : ${DMG}
image-alias     : ${DMG}
image-type      : UDIF read-only [write once]
system-image    : TRUE
process ID      : 95366
/dev/disk14\tGUID_partition_scheme\t
/dev/disk14s1\t7C3457EF-0000-11AA-AA11-00306543ECAC\t
/dev/disk15\tEF57347C-0000-11AA-AA11-00306543ECAC\t
/dev/disk15s1\t41504653-0000-11AA-AA11-00306543ECAC\t
`;

test("finds the lingering mount of the DMG that hdiutil create left behind", () => {
  assert.equal(attachedDevice(info, DMG), "/dev/disk14");
});

test("ignores other images and reports nothing when the DMG is not attached", () => {
  assert.equal(attachedDevice(info, "/elsewhere/VortoCode_0.2.20_aarch64.dmg"), null);
  assert.equal(attachedDevice("framework : 1\nimages : 0\n", DMG), null);
  // 路径只是前缀相同也不能算
  assert.equal(attachedDevice(info, DMG.replace(".dmg", "")), null);
});

test("signs the DMG with the app's Developer ID identity, and refuses anything else", () => {
  const details = "Executable=/x\nAuthority=Developer ID Application: Example Co (TEAMID1234)\nAuthority=Developer ID Certification Authority\n";
  assert.equal(developerIdAuthority(details), "Developer ID Application: Example Co (TEAMID1234)");
  assert.throws(() => developerIdAuthority("Authority=Apple Development: someone (X)\n"), /Developer ID Application/);
});

test("only an Accepted notarization counts", () => {
  assert.deepEqual(notarizationOutcome('{"id":"abc","status":"Accepted"}'), { ok: true, id: "abc" });
  assert.deepEqual(notarizationOutcome('{"id":"abc","status":"Invalid"}'), { ok: false, id: "abc", status: "Invalid" });
  assert.deepEqual(notarizationOutcome("Error: connection timed out"), { ok: false, id: undefined, status: "no result" });
});

test("names the asset the way the release and download page expect", () => {
  assert.equal(dmgAssetName("0.2.20"), "VortoCode_0.2.20_aarch64.dmg");
  assert.throws(() => dmgAssetName("v0.2.20"), /版本号不对/);
});
