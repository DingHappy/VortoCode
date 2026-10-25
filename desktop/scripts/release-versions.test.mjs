import { test } from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { releaseVersions, tomlSectionVersion, versionMismatches } from "./release-versions.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

test("reads the version from the named section only, not from dependency tables", () => {
  const cargo = '[package]\nname = "vortocode-desktop"\nversion = "0.2.20"\n\n[dependencies]\ntauri = { version = "2" }\n';
  assert.equal(tomlSectionVersion(cargo, "package"), "0.2.20");
  const pyproject = '[build-system]\nrequires = ["setuptools"]\n\n[project]\nname = "vortocode"\nversion = "0.2.20"\n\n[tool.x]\nversion = "9"\n';
  assert.equal(tomlSectionVersion(pyproject, "project"), "0.2.20");
  assert.equal(tomlSectionVersion('[tool.x]\nversion = "9"\n', "project"), null);
});

test("names every file that lags behind the app version", () => {
  const versions = { "desktop/package.json": "0.2.20", "desktop/src-tauri/tauri.conf.json": "0.2.20",
    "desktop/src-tauri/Cargo.toml": "0.2.20", "pyproject.toml": "0.2.15" };
  assert.deepEqual(versionMismatches(versions), ["pyproject.toml=0.2.15 (expected 0.2.20)"]);
  assert.deepEqual(versionMismatches({ ...versions, "pyproject.toml": null }), ["pyproject.toml=missing (expected 0.2.20)"]);
  assert.deepEqual(versionMismatches({ ...versions, "pyproject.toml": "0.2.20" }), []);
});

test("the repository's four version numbers agree", () => {
  assert.deepEqual(versionMismatches(releaseVersions(repoRoot)), []);
});
