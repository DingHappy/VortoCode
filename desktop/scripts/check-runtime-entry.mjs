import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { findPythonWithModule } from "./python-tooling.mjs";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { python } = findPythonWithModule("pytest");
const checked = spawnSync(
  python,
  ["-m", "pytest", "../tests/unit/test_desktop_runtime_entry.py"],
  { cwd: desktopRoot, stdio: "inherit" },
);
if (checked.error) throw checked.error;
if (checked.status !== 0) process.exit(checked.status ?? 1);
