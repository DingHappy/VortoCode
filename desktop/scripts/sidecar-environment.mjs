import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ensureManagedPython } from "./managed-python.mjs";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const tauriRoot = join(desktopRoot, "src-tauri");
const lockPath = join(desktopRoot, "requirements-runtime.lock");

function run(command, args, options = {}) {
  const { env, ...spawnOptions } = options;
  const result = spawnSync(command, args, {
    encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", ...env },
    ...spawnOptions,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed\n${result.stderr || result.stdout}`);
  }
  return (result.stdout || result.stderr || "").trim();
}

function normalizedRequirements(content) {
  return content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => line.toLocaleLowerCase("en-US"))
    .sort();
}

function pythonIn(environmentRoot) {
  return process.platform === "win32"
    ? join(environmentRoot, "Scripts", "python.exe")
    : join(environmentRoot, "bin", "python");
}

function inspectPython(python) {
  const raw = run(python, [
    "-c",
    "import json, platform, sys; print(json.dumps({'executable': sys.executable, 'implementation': platform.python_implementation(), 'machine': platform.machine(), 'version': platform.python_version()}))",
  ]);
  const details = JSON.parse(raw);
  const [major, minor] = details.version.split(".").map(Number);
  if (details.implementation !== "CPython" || major < 3 || (major === 3 && minor < 10)) {
    throw new Error(`Desktop sidecar requires CPython 3.10+, received ${details.implementation} ${details.version}`);
  }
  return details;
}

function resolveBootstrapPython(targetTriple) {
  if (process.env.VORTOCODE_SIDECAR_BOOTSTRAP_PYTHON) {
    const python = process.env.VORTOCODE_SIDECAR_BOOTSTRAP_PYTHON;
    return {
      details: inspectPython(python),
      provenance: { provider: "external", targetTriple, verifiedArchive: false },
      python,
    };
  }
  const managed = ensureManagedPython(targetTriple);
  return {
    details: inspectPython(managed.python),
    provenance: managed.manifest,
    python: managed.python,
  };
}

function verifyEnvironment(python, lock) {
  const details = inspectPython(python);
  const pyinstallerVersion = run(python, ["-m", "PyInstaller", "--version"]);
  const frozen = run(python, ["-m", "pip", "freeze", "--exclude-editable"]);
  if (JSON.stringify(normalizedRequirements(frozen)) !== JSON.stringify(normalizedRequirements(lock.toString("utf8")))) {
    throw new Error(`Desktop sidecar environment at ${python} does not exactly match requirements-runtime.lock`);
  }
  return { details, pyinstallerVersion };
}

export function ensureSidecarEnvironment(targetTriple) {
  const lock = readFileSync(lockPath);
  const lockSha256 = createHash("sha256").update(lock).digest("hex");

  if (process.env.VORTOCODE_SIDECAR_PYTHON) {
    const python = process.env.VORTOCODE_SIDECAR_PYTHON;
    const verified = verifyEnvironment(python, lock);
    return {
      lockSha256,
      managed: false,
      pyinstallerVersion: verified.pyinstallerVersion,
      python,
      pythonDetails: verified.details,
      pythonProvenance: { provider: "external", targetTriple, verifiedArchive: false },
    };
  }

  const {
    details: bootstrapDetails,
    provenance: bootstrapProvenance,
    python: bootstrapPython,
  } = resolveBootstrapPython(targetTriple);
  const environmentRoot = join(tauriRoot, "target", "sidecar-python", targetTriple);
  const manifestPath = join(environmentRoot, ".vortocode-runtime-env.json");
  const expected = {
    bootstrapArchiveSha256: bootstrapProvenance.archiveSha256 || null,
    bootstrapConfigSha256: bootstrapProvenance.configSha256 || null,
    bootstrapImplementation: bootstrapDetails.implementation,
    bootstrapMachine: bootstrapDetails.machine,
    bootstrapProvider: bootstrapProvenance.provider,
    bootstrapRelease: bootstrapProvenance.release || null,
    bootstrapRuntimeTreeSha256: bootstrapProvenance.runtimeTreeSha256 || null,
    bootstrapVersion: bootstrapDetails.version,
    bootstrapVerifiedArchive: bootstrapProvenance.verifiedArchive,
    lockSha256,
    targetTriple,
  };

  if (existsSync(manifestPath)) {
    try {
      const current = JSON.parse(readFileSync(manifestPath, "utf8"));
      if (JSON.stringify(current) === JSON.stringify(expected)) {
        const python = pythonIn(environmentRoot);
        const verified = verifyEnvironment(python, lock);
        return {
          lockSha256,
          managed: true,
          pyinstallerVersion: verified.pyinstallerVersion,
          python,
          pythonDetails: verified.details,
          pythonProvenance: bootstrapProvenance,
        };
      }
    } catch {
      // Recreate generated state when its manifest or environment is incomplete.
    }
  }

  const temporaryRoot = `${environmentRoot}.tmp-${process.pid}`;
  rmSync(temporaryRoot, { force: true, recursive: true });
  mkdirSync(dirname(environmentRoot), { recursive: true });
  console.log(`Creating isolated Desktop sidecar environment for ${targetTriple}...`);
  run(bootstrapPython, ["-m", "venv", temporaryRoot], { stdio: "inherit" });
  const temporaryPython = pythonIn(temporaryRoot);
  run(temporaryPython, [
    "-m",
    "pip",
    "install",
    "--disable-pip-version-check",
    "--no-deps",
    "--requirement",
    lockPath,
  ], { stdio: "inherit" });
  run(temporaryPython, ["-m", "pip", "check"], { stdio: "inherit" });
  writeFileSync(join(temporaryRoot, ".vortocode-runtime-env.json"), `${JSON.stringify(expected, null, 2)}\n`);
  rmSync(environmentRoot, { force: true, recursive: true });
  renameSync(temporaryRoot, environmentRoot);

  const python = pythonIn(environmentRoot);
  const verified = verifyEnvironment(python, lock);
  return {
    lockSha256,
    managed: true,
    pyinstallerVersion: verified.pyinstallerVersion,
    python,
    pythonDetails: verified.details,
    pythonProvenance: bootstrapProvenance,
  };
}
