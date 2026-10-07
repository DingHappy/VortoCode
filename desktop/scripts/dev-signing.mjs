import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { RUNTIME_DIR_NAME, machOFiles, runtimeExecutableName, sha256Tree } from "./runtime-tree.mjs";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const tauriRoot = join(desktopRoot, "src-tauri");
const tauriConfig = JSON.parse(readFileSync(join(tauriRoot, "tauri.conf.json"), "utf8"));
const appPath = join(
  tauriRoot,
  "target",
  "release",
  "bundle",
  "macos",
  `${tauriConfig.productName}.app`,
);
const installedAppPath = join("/Applications", `${tauriConfig.productName}.app`);
const evidencePath = join(tauriRoot, "target", "release", "dev-signing-evidence.json");
const sidecarEntitlementsPath = join(tauriRoot, "SidecarEntitlements.plist");
const allowIdentityChange = process.env.VORTOCODE_ALLOW_SIGNING_IDENTITY_CHANGE === "1";
const defaultDevelopmentIdentity = "VortoCode Development";
// 设置后在签名完成后提交 Apple 公证并装订票据（xcrun notarytool store-credentials 存的 profile 名）。
const notaryProfile = (process.env.VORTOCODE_NOTARY_PROFILE || "").trim();

// 内置 runtime 是 onedir 目录，随 resources 放在 Contents/Resources/runtime/。
function runtimeDirIn(app) {
  return join(app, "Contents", "Resources", RUNTIME_DIR_NAME);
}

function runtimeExecutableIn(app) {
  return join(runtimeDirIn(app), runtimeExecutableName());
}

function execute(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: desktopRoot,
    encoding: "utf8",
    ...options,
  });
  if (result.error) throw result.error;
  return result;
}

function required(command, args, options = {}) {
  const result = execute(command, args, options);
  if (result.status !== 0) {
    const output = `${result.stdout || ""}${result.stderr || ""}`.trim();
    throw new Error(`${command} ${args.join(" ")} failed${output ? `\n${output}` : ""}`);
  }
  return `${result.stdout || ""}${result.stderr || ""}`.trim();
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function codeSigningIdentities() {
  const output = required("security", ["find-identity", "-v", "-p", "codesigning"]);
  return output
    .split(/\r?\n/)
    .map((line) => line.match(/^\s*\d+\)\s+([A-Fa-f0-9]{40})\s+"(.+)"\s*$/))
    .filter(Boolean)
    .map((match) => ({ hash: match[1].toUpperCase(), name: match[2] }));
}

function probeLocalDevelopmentIdentity(name) {
  const keychain = join(homedir(), "Library", "Keychains", "login.keychain-db");
  const certificate = execute("security", ["find-certificate", "-a", "-c", name, "-Z", keychain]);
  const privateKey = execute("security", ["find-key", "-l", name, "-t", "private", "-s", keychain]);
  if (certificate.status !== 0 || privateKey.status !== 0) return null;
  const certificateOutput = `${certificate.stdout || ""}${certificate.stderr || ""}`;
  const hash = certificateOutput.match(/^SHA-1 hash:\s*([A-Fa-f0-9]{40})$/m)?.[1]?.toUpperCase();
  if (!hash) return null;

  const work = mkdtempSync(join(tmpdir(), "vortocode-signing-probe-"));
  const probe = join(work, "probe");
  try {
    copyFileSync("/bin/echo", probe);
    const signed = execute("codesign", ["--force", "--sign", name, probe]);
    if (signed.status !== 0) return null;
    const verified = execute("codesign", ["--verify", "--strict", probe]);
    if (verified.status !== 0) return null;
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
  return { hash, name, localDevelopment: true };
}

function identityNamed(name) {
  return codeSigningIdentities().find((identity) => identity.name === name)
    || probeLocalDevelopmentIdentity(name);
}

function selectIdentity() {
  const identities = codeSigningIdentities();
  const requested = (
    process.env.VORTOCODE_CODESIGN_IDENTITY
    || process.env.APPLE_SIGNING_IDENTITY
    || ""
  ).trim();
  if (requested === "-") {
    throw new Error("Ad-hoc signing is intentionally rejected because it cannot provide a stable Keychain identity");
  }
  if (requested) {
    const match = identities.find(
      (identity) => identity.hash === requested.toUpperCase() || identity.name === requested,
    ) || probeLocalDevelopmentIdentity(requested);
    if (!match) {
      throw new Error(`Requested code-signing identity was not found: ${requested}`);
    }
    return match;
  }
  // Prefer a single Developer ID Application identity over the local self-signed default.
  // Keychain partition lists only match a stable "teamid:" for Apple-issued certificates;
  // a self-signed certificate has no Team ID, so each rebuild gets a new cdhash partition
  // and macOS asks for Keychain access again after every reinstall.
  const developerIds = identities.filter((identity) => identity.name.startsWith("Developer ID Application:"));
  if (developerIds.length === 1) return developerIds[0];
  const localDefault = identityNamed(defaultDevelopmentIdentity);
  if (localDefault) return localDefault;
  if (identities.length === 1) return identities[0];
  if (identities.length === 0) {
    throw new Error([
      "No valid macOS code-signing identity was found.",
      "Create a persistent Code Signing certificate in Keychain Access, then run:",
      "  VORTOCODE_CODESIGN_IDENTITY=\"VortoCode Development\" npm run signing:doctor",
    ].join("\n"));
  }
  throw new Error([
    "Multiple valid code-signing identities were found.",
    "Select one explicitly with VORTOCODE_CODESIGN_IDENTITY before building.",
    ...identities.map((identity) => `  ${identity.hash}  ${identity.name}`),
  ].join("\n"));
}

function setupIdentity() {
  const identityName = (
    process.env.VORTOCODE_CODESIGN_IDENTITY
    || defaultDevelopmentIdentity
  ).trim();
  if (!identityName || identityName.length > 128 || /[\r\n]/.test(identityName)) {
    throw new Error("VORTOCODE_CODESIGN_IDENTITY is not a valid certificate name");
  }
  const existing = identityNamed(identityName);
  if (existing) {
    console.log(`Stable signing identity already exists: ${existing.name} (${existing.hash})`);
    return existing;
  }

  const work = mkdtempSync(join(tmpdir(), "vortocode-signing-"));
  const configPath = join(work, "certificate.cnf");
  const keyPath = join(work, "key.pem");
  const certificatePath = join(work, "certificate.pem");
  const archivePath = join(work, "identity.p12");
  const archivePassword = randomBytes(24).toString("hex");
  try {
    writeFileSync(configPath, [
      "[req]",
      "distinguished_name=dn",
      "x509_extensions=ext",
      "prompt=no",
      "[dn]",
      `CN=${identityName}`,
      "[ext]",
      "basicConstraints=critical,CA:false",
      "keyUsage=critical,digitalSignature",
      "extendedKeyUsage=critical,codeSigning",
      "",
    ].join("\n"), { mode: 0o600 });
    required("openssl", [
      "req", "-x509", "-newkey", "rsa:2048",
      "-keyout", keyPath,
      "-out", certificatePath,
      "-days", "3650",
      "-nodes",
      "-config", configPath,
    ]);
    required("openssl", [
      "pkcs12", "-export", "-legacy",
      "-out", archivePath,
      "-inkey", keyPath,
      "-in", certificatePath,
      "-name", identityName,
      "-passout", `pass:${archivePassword}`,
    ]);
    required("security", [
      "import", archivePath,
      "-k", join(homedir(), "Library", "Keychains", "login.keychain-db"),
      "-P", archivePassword,
      "-T", "/usr/bin/codesign",
    ]);
  } finally {
    rmSync(work, { recursive: true, force: true });
  }

  const created = identityNamed(identityName);
  if (!created) {
    throw new Error("The certificate was imported but macOS did not register it as a valid code-signing identity");
  }
  console.log(`Created stable signing identity: ${created.name} (${created.hash})`);
  console.log("The private key remains in the login Keychain; temporary source files were deleted.");
  return created;
}

function signatureDetails(path) {
  if (!existsSync(path)) throw new Error(`App bundle is missing: ${path}`);
  const executable = join(path, "Contents", "MacOS", "vortocode-desktop");
  const sidecar = runtimeExecutableIn(path);
  if (!existsSync(executable) || !existsSync(sidecar)) {
    throw new Error(`Signed bundle is missing the main executable or runtime sidecar: ${path}`);
  }

  for (const binary of machOFiles(runtimeDirIn(path))) {
    required("codesign", ["--verify", "--strict", "--verbose=2", binary]);
  }
  required("codesign", ["--verify", "--deep", "--strict", "--verbose=2", path]);
  const appDetails = required("codesign", ["-dv", "--verbose=4", path]);
  const sidecarDetails = required("codesign", ["-dv", "--verbose=4", sidecar]);
  if (/Signature=adhoc/i.test(appDetails) || /Signature=adhoc/i.test(sidecarDetails)) {
    throw new Error("Ad-hoc signatures are not accepted for VortoCode development installs");
  }
  const sidecarEntitlements = required("codesign", ["-d", "--entitlements", "-", sidecar]);
  if (!sidecarEntitlements.includes("com.apple.security.cs.disable-library-validation")) {
    throw new Error("Signed PyInstaller sidecar is missing its required library-validation entitlement");
  }
  const identifier = appDetails.match(/^Identifier=(.+)$/m)?.[1]?.trim();
  if (identifier !== tauriConfig.identifier) {
    throw new Error(`Unexpected signed bundle identifier: ${identifier || "missing"}`);
  }
  const requirementOutput = required("codesign", ["-d", "-r-", path]);
  const designatedRequirement = requirementOutput
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^#\s*/, ""))
    .find((line) => line.startsWith("designated =>"));
  if (!designatedRequirement) {
    throw new Error("Signed app has no designated requirement");
  }
  const authorities = [...appDetails.matchAll(/^Authority=(.+)$/gm)].map((match) => match[1].trim());
  return {
    appSha256: sha256(executable),
    authorities,
    designatedRequirement,
    identifier,
    sidecarEntitlements: ["com.apple.security.cs.disable-library-validation"],
    sidecarSha256: sha256Tree(runtimeDirIn(path)),
  };
}

function resignPyInstallerBundle(path, identity) {
  const sidecar = runtimeExecutableIn(path);
  if (!existsSync(sidecarEntitlementsPath)) {
    throw new Error(`Sidecar entitlements file is missing: ${sidecarEntitlementsPath}`);
  }
  // Tauri copies resources without signing them. Sign every Mach-O in the
  // onedir runtime (libpython, extension modules) with Hardened Runtime, give
  // the runtime executable its one narrow library-validation exception, then
  // reseal the outer bundle without broadening the WebView host's entitlements.
  // 公证要求安全时间戳；只有 Apple 签发的证书能拿到，本地自签身份不加。
  const timestamp = identity.name.startsWith("Developer ID Application:") ? ["--timestamp"] : [];
  for (const binary of machOFiles(runtimeDirIn(path)).filter((binary) => binary !== sidecar)) {
    required("codesign", ["--force", "--options", "runtime", ...timestamp, "--sign", identity.name, binary]);
  }
  required("codesign", [
    "--force", "--options", "runtime", ...timestamp,
    "--entitlements", sidecarEntitlementsPath,
    "--sign", identity.name,
    sidecar,
  ]);
  required("codesign", [
    "--force", "--options", "runtime", ...timestamp,
    "--sign", identity.name,
    path,
  ]);
}

// Tauri 自己的公证发生在我们重签之前，重签后就失效了；所以在全部签名完成后再提交。
function notarize(path, identity) {
  if (!identity.name.startsWith("Developer ID Application:")) {
    throw new Error("Notarization requires a Developer ID Application identity");
  }
  const work = mkdtempSync(join(tmpdir(), "vortocode-notary-"));
  const archive = join(work, "VortoCode.zip");
  try {
    required("ditto", ["-c", "-k", "--keepParent", path, archive]);
    console.log(`Submitting ${path} for notarization (profile ${notaryProfile}); this usually takes a few minutes...`);
    const submitted = execute("xcrun", [
      "notarytool", "submit", archive,
      "--keychain-profile", notaryProfile,
      "--wait", "--output-format", "json",
    ]);
    let result = {};
    try {
      result = JSON.parse(submitted.stdout || "{}");
    } catch {
      // 非 JSON 输出（例如网络错误）落到下面的失败分支。
    }
    if (result.status !== "Accepted") {
      const log = result.id
        ? execute("xcrun", ["notarytool", "log", result.id, "--keychain-profile", notaryProfile]).stdout
        : "";
      throw new Error([
        `Notarization failed: ${result.status || "no result"}${result.id ? ` (submission ${result.id})` : ""}`,
        `${submitted.stdout || ""}${submitted.stderr || ""}`.trim(),
        log.trim(),
      ].filter(Boolean).join("\n"));
    }
    console.log(`Notarization accepted (submission ${result.id})`);
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
  required("xcrun", ["stapler", "staple", path]);
  required("xcrun", ["stapler", "validate", path]);
  required("spctl", ["--assess", "--type", "execute", "--verbose=4", path]);
  console.log("Stapled notarization ticket; Gatekeeper accepts the app");
}

function existingSignatureDetails(path) {
  if (!existsSync(path)) return null;
  try {
    return signatureDetails(path);
  } catch {
    return null;
  }
}

function assertStableRequirement(previous, current, label) {
  if (!previous || previous.designatedRequirement === current.designatedRequirement) return;
  if (allowIdentityChange) {
    console.warn(`Warning: ${label} designated requirement changed by explicit override`);
    return;
  }
  throw new Error([
    `${label} uses a different designated requirement; refusing to replace its Keychain identity.`,
    `Previous: ${previous.designatedRequirement}`,
    `Current:  ${current.designatedRequirement}`,
    "Set VORTOCODE_ALLOW_SIGNING_IDENTITY_CHANGE=1 only for an intentional certificate migration.",
  ].join("\n"));
}

function previousEvidence() {
  if (!existsSync(evidencePath)) return null;
  try {
    return JSON.parse(readFileSync(evidencePath, "utf8"));
  } catch {
    return null;
  }
}

function writeEvidence(identity, signature) {
  mkdirSync(dirname(evidencePath), { recursive: true });
  writeFileSync(evidencePath, `${JSON.stringify({
    formatVersion: 1,
    generatedAt: new Date().toISOString(),
    identity,
    product: {
      identifier: tauriConfig.identifier,
      name: tauriConfig.productName,
      version: tauriConfig.version,
    },
    signature,
  }, null, 2)}\n`);
}

function doctor() {
  const identity = selectIdentity();
  console.log(`Stable signing identity: ${identity.name} (${identity.hash})`);
  console.log(`Bundle identifier: ${tauriConfig.identifier}`);
  const installed = existingSignatureDetails(installedAppPath);
  if (installed) {
    console.log(`Installed designated requirement: ${installed.designatedRequirement}`);
  } else if (existsSync(installedAppPath)) {
    console.warn("Warning: the installed VortoCode.app is unsigned or has an invalid signature");
  }
  return identity;
}

function buildSigned() {
  const identity = doctor();
  const baseline = previousEvidence();
  required("npm", ["run", "sidecar:build"], { stdio: "inherit" });
  required("npm", ["run", "sidecar:smoke"], { stdio: "inherit" });
  required("npx", ["tauri", "build", "--bundles", "app"], {
    env: { ...process.env, APPLE_SIGNING_IDENTITY: identity.name },
    stdio: "inherit",
  });
  resignPyInstallerBundle(appPath, identity);
  const signature = signatureDetails(appPath);
  if (notaryProfile) notarize(appPath, identity);
  if (baseline?.identity?.hash && baseline.identity.hash !== identity.hash && !allowIdentityChange) {
    throw new Error("The selected signing certificate differs from the previous signed development build");
  }
  assertStableRequirement(baseline?.signature, signature, "Previous development build");
  assertStableRequirement(existingSignatureDetails(installedAppPath), signature, "Installed application");
  writeEvidence(identity, signature);
  console.log(`Signed VortoCode app built at ${appPath}`);
  console.log(`Designated requirement: ${signature.designatedRequirement}`);
  return signature;
}

function installSigned() {
  const signature = buildSigned();
  // ditto 会把新包**合并**进已有目录，旧版留下的文件（例如 one-file 时代的
  // Contents/MacOS/vortocode-runtime）会破坏签名封装。先复制到同卷的临时位置，再整体替换。
  const staging = `${installedAppPath}.installing-${process.pid}`;
  rmSync(staging, { recursive: true, force: true });
  required("ditto", [appPath, staging], { stdio: "inherit" });
  rmSync(installedAppPath, { recursive: true, force: true });
  renameSync(staging, installedAppPath);
  const installed = signatureDetails(installedAppPath);
  assertStableRequirement(signature, installed, "Installed application");
  if (signature.appSha256 !== installed.appSha256 || signature.sidecarSha256 !== installed.sidecarSha256) {
    throw new Error("Installed application does not match the signed build output");
  }
  required("npm", ["run", "sidecar:smoke"], {
    env: {
      ...process.env,
      VORTOCODE_RUNTIME_BIN: runtimeExecutableIn(installedAppPath),
    },
    stdio: "inherit",
  });
  console.log(`Installed signed application at ${installedAppPath}`);
  console.log("Fully quit the running app once before reopening it so macOS loads the new signature.");
}

function verifyInstalled() {
  const signature = signatureDetails(installedAppPath);
  console.log(`Installed bundle identifier: ${signature.identifier}`);
  console.log(`Installed authorities: ${signature.authorities.join(" > ") || "unavailable"}`);
  console.log(`Installed designated requirement: ${signature.designatedRequirement}`);
}

const action = process.argv[2] || "doctor";
try {
  if (action === "setup") setupIdentity();
  else if (action === "doctor") doctor();
  else if (action === "build") buildSigned();
  else if (action === "install") installSigned();
  else if (action === "verify") verifyInstalled();
  else throw new Error(`Unknown action: ${action}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}
