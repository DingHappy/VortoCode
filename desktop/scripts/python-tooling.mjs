import { spawnSync } from "node:child_process";
import { join } from "node:path";

export function findPythonWithModule(moduleName) {
  const executable = process.platform === "win32" ? "python.exe" : "python";
  const candidates = [
    process.env.VORTOCODE_PYTHON,
    process.env.VIRTUAL_ENV && join(process.env.VIRTUAL_ENV, process.platform === "win32" ? "Scripts" : "bin", executable),
    process.env.CONDA_PYTHON_EXE,
    process.env.CONDA_PREFIX && join(process.env.CONDA_PREFIX, process.platform === "win32" ? "python.exe" : "bin/python"),
    process.platform === "win32" ? "python" : "python3",
    "python",
  ].filter(Boolean);

  const failures = [];
  for (const python of [...new Set(candidates)]) {
    const module = spawnSync(python, ["-m", moduleName, "--version"], { encoding: "utf8" });
    if (!module.error && module.status === 0) {
      const version = spawnSync(python, ["--version"], { encoding: "utf8" });
      return {
        moduleVersion: `${module.stdout || ""}${module.stderr || ""}`.trim(),
        python,
        pythonVersion: `${version.stdout || ""}${version.stderr || ""}`.trim(),
      };
    }
    failures.push(`${python}: ${(module.error?.message || module.stderr || "module unavailable").trim()}`);
  }
  throw new Error([
    `No Python interpreter with ${moduleName} is available.`,
    `Set VORTOCODE_PYTHON or install it with: python -m pip install ${moduleName}`,
    ...failures,
  ].join("\n"));
}
