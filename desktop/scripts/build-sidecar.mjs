import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { delimiter, dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ensureSidecarEnvironment } from "./sidecar-environment.mjs";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(desktopRoot, "..");
const tauriRoot = join(desktopRoot, "src-tauri");

function output(command, args) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed\n${result.stderr || result.stdout}`);
  }
  return (result.stdout || result.stderr || "").trim();
}

function sourceFiles(path) {
  if (!statSync(path).isDirectory()) return [path];
  return readdirSync(path, { withFileTypes: true })
    .filter((entry) => entry.name !== "__pycache__" && entry.name !== ".DS_Store")
    .flatMap((entry) => sourceFiles(join(path, entry.name)))
    .filter((entry) => extname(entry) !== ".pyc");
}

const targetTriple = output("rustc", ["--print", "host-tuple"]);
const extension = process.platform === "win32" ? ".exe" : "";
const binaryName = `vortocode-runtime-${targetTriple}${extension}`;
const binaryDir = join(tauriRoot, "binaries");
const binaryPath = join(binaryDir, binaryName);
const buildRoot = join(tauriRoot, "target", "sidecar", targetTriple);
const manifestPath = join(buildRoot, "manifest.json");
const inventoryPath = join(buildRoot, "third-party-inventory.json");
const noticesPath = join(tauriRoot, "resources", "THIRD_PARTY_NOTICES.txt");
const sidecarEnvironment = ensureSidecarEnvironment(targetTriple);
const { pyinstallerVersion, python } = sidecarEnvironment;
const pythonVersion = `${sidecarEnvironment.pythonDetails.implementation} ${sidecarEnvironment.pythonDetails.version}`;
const environmentFingerprint = output(python, ["-m", "pip", "freeze"]);

const fingerprint = createHash("sha256");
fingerprint.update(targetTriple);
fingerprint.update(pythonVersion);
fingerprint.update(pyinstallerVersion);
fingerprint.update(createHash("sha256").update(environmentFingerprint).digest("hex"));

const inputs = [
  join(repoRoot, "src"),
  join(repoRoot, "web"),
  join(repoRoot, "config"),
  join(repoRoot, "skills"),
  join(repoRoot, "pyproject.toml"),
  join(desktopRoot, "managed-python-sources.json"),
  join(desktopRoot, "requirements-runtime.in"),
  join(desktopRoot, "requirements-runtime.lock"),
  join(desktopRoot, "runtime_entry.py"),
  join(desktopRoot, "scripts", "python-tooling.mjs"),
  join(desktopRoot, "scripts", "generate_third_party_notices.py"),
  join(desktopRoot, "scripts", "managed-python.mjs"),
  join(desktopRoot, "scripts", "sidecar-environment.mjs"),
  fileURLToPath(import.meta.url),
];
for (const file of inputs.flatMap(sourceFiles).sort()) {
  fingerprint.update(relative(repoRoot, file));
  fingerprint.update(readFileSync(file));
}
const digest = fingerprint.digest("hex");

if (existsSync(binaryPath) && existsSync(manifestPath) && existsSync(inventoryPath) && existsSync(noticesPath)) {
  const cached = JSON.parse(readFileSync(manifestPath, "utf8"));
  if (cached.fingerprint === digest && cached.binary === binaryName) {
    console.log(`Desktop runtime sidecar is current (${binaryName}, ${cached.size} bytes)`);
    process.exit(0);
  }
}

mkdirSync(binaryDir, { recursive: true });
mkdirSync(buildRoot, { recursive: true });
const distPath = join(buildRoot, "dist");
const workPath = join(buildRoot, "work");
const specPath = join(buildRoot, "spec");
rmSync(distPath, { recursive: true, force: true });
rmSync(workPath, { recursive: true, force: true });
rmSync(specPath, { recursive: true, force: true });
mkdirSync(distPath, { recursive: true });
mkdirSync(workPath, { recursive: true });
mkdirSync(specPath, { recursive: true });

const excludedModules = [
  "IPython",
  "PIL",
  "anthropic",
  "docutils",
  "matplotlib",
  "mcp",
  "numpy",
  "openai",
  "playwright",
  "qdrant_client",
  "rich",
  "sentence_transformers",
  "sphinx",
  "textual",
  "torch",
  "transformers",
  "tree_sitter",
];
const args = [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onefile",
  "--noupx",
  "--log-level",
  "WARN",
  "--name",
  "vortocode-runtime",
  "--distpath",
  distPath,
  "--workpath",
  workPath,
  "--specpath",
  specPath,
  "--paths",
  repoRoot,
];
for (const [source, destination] of [
  [join(repoRoot, "web"), "web"],
  [join(repoRoot, "config"), "config"],
  [join(repoRoot, "skills"), "skills"],
]) {
  args.push("--add-data", `${source}${delimiter}${destination}`);
}
for (const module of excludedModules) args.push("--exclude-module", module);
args.push(join(desktopRoot, "runtime_entry.py"));

console.log(`Building Desktop runtime sidecar for ${targetTriple} with ${pythonVersion}...`);
const built = spawnSync(python, args, {
  cwd: repoRoot,
  env: {
    ...process.env,
    PYINSTALLER_CONFIG_DIR: join(buildRoot, "pyinstaller-cache"),
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONHASHSEED: "0",
  },
  stdio: "inherit",
});
if (built.error) throw built.error;
if (built.status !== 0) throw new Error(`PyInstaller failed with exit code ${built.status}`);

const builtPath = join(distPath, `vortocode-runtime${extension}`);
if (!existsSync(builtPath) || statSync(builtPath).size < 1024 * 1024) {
  throw new Error(`PyInstaller did not produce a valid sidecar at ${builtPath}`);
}
const temporary = `${binaryPath}.${process.pid}.tmp`;
copyFileSync(builtPath, temporary);
if (process.platform !== "win32") chmodSync(temporary, 0o755);
rmSync(binaryPath, { force: true });
renameSync(temporary, binaryPath);

const size = statSync(binaryPath).size;
const analysisPath = join(workPath, "vortocode-runtime", "Analysis-00.toc");
const notices = spawnSync(python, [
  join(desktopRoot, "scripts", "generate_third_party_notices.py"),
  "--analysis",
  analysisPath,
  "--inventory",
  inventoryPath,
  "--notices",
  noticesPath,
], {
  cwd: repoRoot,
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  stdio: "inherit",
});
if (notices.error) throw notices.error;
if (notices.status !== 0) throw new Error(`Third-party notice generation failed with exit code ${notices.status}`);
const inventory = JSON.parse(readFileSync(inventoryPath, "utf8"));
const manifest = {
  binary: binaryName,
  binarySha256: createHash("sha256").update(readFileSync(binaryPath)).digest("hex"),
  fingerprint: digest,
  noticesSha256: createHash("sha256").update(readFileSync(noticesPath)).digest("hex"),
  pyinstaller: pyinstallerVersion,
  python: pythonVersion,
  pythonDistribution: inventory.pythonRuntime.distribution,
  pythonMachine: sidecarEnvironment.pythonDetails.machine,
  pythonProvenance: sidecarEnvironment.pythonProvenance,
  pythonProvider: inventory.pythonRuntime.provider,
  runtimeLockSha256: sidecarEnvironment.lockSha256,
  runtimeEnvironment: sidecarEnvironment.managed ? "managed" : "external",
  size,
  targetTriple,
  thirdPartyComponentCount: inventory.componentCount,
  thirdPartyInventorySha256: createHash("sha256").update(readFileSync(inventoryPath)).digest("hex"),
};
const manifestTemporary = `${manifestPath}.${process.pid}.tmp`;
writeFileSync(manifestTemporary, `${JSON.stringify(manifest, null, 2)}\n`);
renameSync(manifestTemporary, manifestPath);
console.log(`Built Desktop runtime sidecar: ${binaryPath} (${size} bytes)`);
