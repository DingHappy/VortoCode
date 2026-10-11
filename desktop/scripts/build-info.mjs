import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, readdirSync } from "node:fs";
import { join, relative, resolve } from "node:path";

/** Fingerprint product inputs, including uncommitted files; never include credentials. */
export function createBuildInfo(desktopRoot, now = new Date()) {
  const repo = resolve(desktopRoot, "..");
  const version = JSON.parse(readFileSync(join(desktopRoot, "src-tauri/tauri.conf.json"), "utf8")).version;
  const packageVersion = JSON.parse(readFileSync(join(desktopRoot, "package.json"), "utf8")).version;
  if (version !== packageVersion) throw new Error("Desktop package and Tauri versions differ");
  const hash = createHash("sha256");
  const visit = path => {
    if (!existsSync(path)) { hash.update(relative(repo, path)).update("\0missing\0"); return; }
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) throw new Error("Build inputs must not be symlinks");
    if (stat.isDirectory()) {
      for (const name of readdirSync(path).sort()) {
        if (["__pycache__", ".pytest_cache", "node_modules", "target"].includes(name) || name.endsWith(".pyc")) continue;
        visit(join(path, name));
      }
    } else if (stat.isFile()) {
      hash.update(relative(repo, path).split("\\").join("/")).update("\0")
        .update(createHash("sha256").update(readFileSync(path)).digest()).update("\0");
    }
  };
  for (const path of ["src", "desktop/src", "desktop/scripts", "desktop/src-tauri/src", "desktop/src-tauri/icons",
    "desktop/src-tauri/build.rs", "desktop/src-tauri/tauri.conf.json", "desktop/src-tauri/Cargo.toml",
    "desktop/src-tauri/Cargo.lock", "desktop/package.json", "desktop/package-lock.json", "desktop/vite.config.ts",
    "desktop/runtime_entry.py", "desktop/requirements-runtime.in", "desktop/requirements-runtime.lock", "pyproject.toml"]) visit(join(repo, path));
  const sourceFingerprint = hash.digest("hex");
  const builtAt = now.toISOString();
  return { version, builtAt, sourceFingerprint,
    buildId: `${builtAt.replace(/[-:.]/g, "")}-${sourceFingerprint.slice(0, 8)}` };
}
