import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { RUNTIME_DIR_NAME, filesBelow, machOFiles, runtimeExecutableName, sha256Tree, treeSize } from "./runtime-tree.mjs";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(desktopRoot, "..");
const tauriRoot = join(desktopRoot, "src-tauri");

function command(commandName, args, options = {}) {
  const result = spawnSync(commandName, args, {
    cwd: repoRoot,
    encoding: "utf8",
    ...options,
  });
  return {
    output: `${result.stdout || ""}${result.stderr || ""}`.trim(),
    status: result.error ? null : result.status,
  };
}

function requiredOutput(commandName, args) {
  const result = command(commandName, args);
  if (result.status !== 0) {
    throw new Error(`${commandName} ${args.join(" ")} failed\n${result.output}`);
  }
  return result.output;
}

function sha256File(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function findFile(root, name) {
  return filesBelow(root).find((path) => basename(path) === name);
}

function parseArgs() {
  const args = process.argv.slice(2);
  let mode = "preview";
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--mode") mode = args[index + 1] || "";
  }
  if (!new Set(["preview", "release"]).has(mode)) {
    throw new Error("--mode must be preview or release");
  }
  return { mode };
}

const { mode } = parseArgs();
if (process.platform !== "darwin") {
  throw new Error("The current release verifier supports macOS bundles; add a platform verifier before publishing another target");
}

const tauriConfig = JSON.parse(readFileSync(join(tauriRoot, "tauri.conf.json"), "utf8"));
const targetTriple = requiredOutput("rustc", ["--print", "host-tuple"]);
const arch = targetTriple.split("-")[0];
const expectedFileArchitecture = arch === "aarch64" ? "arm64" : arch;
const dmgArchitecture = arch === "x86_64" ? "x64" : arch;
const appPath = join(tauriRoot, "target", "release", "bundle", "macos", `${tauriConfig.productName}.app`);
const dmgPath = join(
  tauriRoot,
  "target",
  "release",
  "bundle",
  "dmg",
  `${tauriConfig.productName}_${tauriConfig.version}_${dmgArchitecture}.dmg`,
);
if (!existsSync(appPath)) throw new Error(`macOS app bundle is missing: ${appPath}`);

const executablePath = join(appPath, "Contents", "MacOS", "vortocode-desktop");
// 内置 runtime 是 onedir 目录，随 resources 放在 Contents/Resources/runtime/。
const runtimeDir = join(appPath, "Contents", "Resources", RUNTIME_DIR_NAME);
const sidecarPath = join(runtimeDir, runtimeExecutableName());
const noticesPath = findFile(join(appPath, "Contents", "Resources"), "THIRD_PARTY_NOTICES.txt");
for (const [label, path] of [["main executable", executablePath], ["sidecar", sidecarPath], ["third-party notices", noticesPath]]) {
  if (!path || !existsSync(path)) throw new Error(`${label} is missing from ${appPath}`);
}

const sidecarManifestPath = join(tauriRoot, "target", "sidecar", targetTriple, "manifest.json");
const sidecarManifest = JSON.parse(readFileSync(sidecarManifestPath, "utf8"));
const managedPythonSourcesPath = join(desktopRoot, "managed-python-sources.json");
const managedPythonSources = JSON.parse(readFileSync(managedPythonSourcesPath, "utf8"));
const managedPythonSourcesSha256 = sha256File(managedPythonSourcesPath);
const pinnedPythonSource = managedPythonSources.sources[targetTriple];
const sourceSidecarPath = join(tauriRoot, "binaries", sidecarManifest.binary);
const sourceRuntimeDir = join(tauriRoot, "binaries", RUNTIME_DIR_NAME);
const sourceNotices = join(tauriRoot, "resources", "THIRD_PARTY_NOTICES.txt");
const architectureEvidence = {
  executable: requiredOutput("file", [executablePath]),
  sidecar: requiredOutput("file", [sidecarPath]),
};
if (!architectureEvidence.executable.includes(expectedFileArchitecture) || !architectureEvidence.sidecar.includes(expectedFileArchitecture)) {
  throw new Error(`Bundle architecture does not match ${targetTriple}`);
}
if (sidecarManifest.layout !== "onedir"
  || !existsSync(sourceSidecarPath)
  || treeSize(sourceRuntimeDir) !== sidecarManifest.size
  || sha256Tree(sourceRuntimeDir) !== sidecarManifest.runtimeSha256Tree) {
  throw new Error("Source sidecar does not match the frozen sidecar manifest");
}
if (sha256File(noticesPath) !== sidecarManifest.noticesSha256 || sha256File(sourceNotices) !== sidecarManifest.noticesSha256) {
  throw new Error("Bundled third-party notices do not match the frozen sidecar manifest");
}

const codeSignature = command("codesign", ["--verify", "--deep", "--strict", "--verbose=2", appPath]);
const codeSignatureDetails = command("codesign", ["-dv", "--verbose=4", appPath]);
const signed = codeSignature.status === 0 && !/Signature=adhoc/i.test(codeSignatureDetails.output);
const bundledSidecarSignature = command("codesign", ["--verify", "--strict", "--verbose=2", sidecarPath]);
const bundledSidecarSignatureDetails = command("codesign", ["-dv", "--verbose=4", sidecarPath]);
// 公证要求 runtime 目录里的每个 Mach-O（libpython、扩展模块）都带非 ad-hoc 签名，不只是入口。
const runtimeBinariesSigned = machOFiles(runtimeDir).every((binary) => {
  const verified = command("codesign", ["--verify", "--strict", binary]);
  const details = command("codesign", ["-dv", "--verbose=4", binary]);
  return verified.status === 0 && !/Signature=adhoc/i.test(details.output);
});
const sidecarSigned = bundledSidecarSignature.status === 0
  && !/Signature=adhoc/i.test(bundledSidecarSignatureDetails.output)
  && runtimeBinariesSigned;
if (signed) {
  if (!sidecarSigned) {
    throw new Error("Signed app contains a sidecar without a non-ad-hoc nested code signature");
  }
} else if (treeSize(runtimeDir) !== sidecarManifest.size
  || sha256Tree(runtimeDir) !== sidecarManifest.runtimeSha256Tree) {
  throw new Error("Unsigned bundled sidecar does not exactly match the frozen sidecar manifest");
}
const notarization = command("xcrun", ["stapler", "validate", appPath]);
const notarized = notarization.status === 0;
const gatekeeper = command("spctl", ["--assess", "--type", "execute", "--verbose=4", appPath]);
const gatekeeperAccepted = gatekeeper.status === 0;
let dmg = null;
if (existsSync(dmgPath)) {
  const dmgSignature = command("codesign", ["--verify", "--strict", "--verbose=2", dmgPath]);
  const dmgSignatureDetails = command("codesign", ["-dv", "--verbose=4", dmgPath]);
  const dmgSigned = dmgSignature.status === 0 && !/Signature=adhoc/i.test(dmgSignatureDetails.output);
  const dmgNotarized = command("xcrun", ["stapler", "validate", dmgPath]).status === 0;
  const dmgGatekeeperAccepted = command("spctl", [
    "--assess",
    "--type",
    "open",
    "--context",
    "context:primary-signature",
    "--verbose=4",
    dmgPath,
  ]).status === 0;
  dmg = {
    gatekeeperAccepted: dmgGatekeeperAccepted,
    hdiutilValid: command("hdiutil", ["verify", dmgPath]).status === 0,
    notarized: dmgNotarized,
    path: relative(repoRoot, dmgPath),
    sha256: sha256File(dmgPath),
    signed: dmgSigned,
    size: statSync(dmgPath).size,
  };
}
const gitStatus = requiredOutput("git", ["status", "--porcelain"]);
const cleanWorktree = gitStatus.length === 0;
const commit = requiredOutput("git", ["rev-parse", "HEAD"]);
const pythonProvenanceTrusted = sidecarManifest.pythonDistribution === "cpython"
  && sidecarManifest.pythonProvider === managedPythonSources.provider
  && sidecarManifest.pythonProvenance?.provider === managedPythonSources.provider
  && sidecarManifest.pythonProvenance?.release === managedPythonSources.release
  && sidecarManifest.pythonProvenance?.pythonVersion === managedPythonSources.pythonVersion
  && sidecarManifest.pythonProvenance?.targetTriple === targetTriple
  && sidecarManifest.pythonProvenance?.verifiedArchive === true
  && sidecarManifest.pythonProvenance?.archive === pinnedPythonSource?.archive
  && sidecarManifest.pythonProvenance?.archiveSha256 === pinnedPythonSource?.sha256
  && sidecarManifest.pythonProvenance?.configSha256 === managedPythonSourcesSha256
  && /^[a-f0-9]{64}$/.test(sidecarManifest.pythonProvenance?.runtimeTreeSha256 || "");

const warnings = [];
if (!signed) warnings.push("macOS app is not signed with a non-ad-hoc identity");
if (!notarized) warnings.push("macOS app has no valid stapled notarization ticket");
if (!gatekeeperAccepted) warnings.push("Gatekeeper assessment did not accept the app");
if (!cleanWorktree) warnings.push("Git worktree contains uncommitted changes");
if (!dmg) warnings.push("DMG is missing");
else {
  if (!dmg.hdiutilValid) warnings.push("DMG failed hdiutil verification");
  if (!dmg.signed) warnings.push("DMG is not signed with a non-ad-hoc identity");
  if (!dmg.notarized) warnings.push("DMG has no valid stapled notarization ticket");
  if (!dmg.gatekeeperAccepted) warnings.push("Gatekeeper assessment did not accept the DMG");
}
if (!pythonProvenanceTrusted) warnings.push("sidecar Python provenance does not match the pinned, SHA-256 verified standalone CPython source");

const releaseReady = signed
  && notarized
  && gatekeeperAccepted
  && cleanWorktree
  && Boolean(dmg?.hdiutilValid && dmg.signed && dmg.notarized && dmg.gatekeeperAccepted)
  && pythonProvenanceTrusted;
const evidence = {
  architecture: architectureEvidence,
  artifacts: {
    app: {
      path: relative(repoRoot, appPath),
      sha256Tree: sha256Tree(appPath),
      size: filesBelow(appPath).reduce((total, path) => total + statSync(path).size, 0),
    },
    dmg,
    notices: {
      path: relative(repoRoot, noticesPath),
      sha256: sha256File(noticesPath),
    },
    sidecar: {
      codeSignatureValid: bundledSidecarSignature.status === 0,
      path: relative(repoRoot, runtimeDir),
      sha256Tree: sha256Tree(runtimeDir),
      signed: sidecarSigned,
      size: treeSize(runtimeDir),
    },
  },
  formatVersion: 1,
  generatedAt: new Date().toISOString(),
  git: { cleanWorktree, commit },
  mode,
  product: { identifier: tauriConfig.identifier, name: tauriConfig.productName, version: tauriConfig.version },
  releaseGates: {
    gatekeeperAccepted,
    notarized,
    pythonProvenanceTrusted,
    releaseReady,
    sidecarSigned,
    signed,
  },
  sidecarBuild: sidecarManifest,
  targetTriple,
  warnings,
};
const evidenceRoot = join(tauriRoot, "target", "release", "release-evidence");
mkdirSync(evidenceRoot, { recursive: true });
const evidencePath = join(evidenceRoot, `${tauriConfig.productName}_${tauriConfig.version}_${arch}_${mode}.json`);
writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);

console.log(`Release evidence written to ${evidencePath}`);
console.log(`Core bundle evidence passed; releaseReady=${releaseReady}`);
for (const warning of warnings) console.warn(`Warning: ${warning}`);
if (mode === "release" && !releaseReady) {
  console.error("Formal release gates failed; inspect the generated release evidence");
  process.exitCode = 1;
}
