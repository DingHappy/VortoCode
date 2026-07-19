import { spawnSync } from "node:child_process";

import { prepareManagedPython } from "./managed-python.mjs";

function hostTargetTriple() {
  const result = spawnSync("rustc", ["--print", "host-tuple"], { encoding: "utf8" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr || "rustc --print host-tuple failed");
  return result.stdout.trim();
}

const args = process.argv.slice(2);
const targetIndex = args.indexOf("--target");
const targetTriple = targetIndex >= 0 ? args[targetIndex + 1] : hostTargetTriple();
if (!targetTriple) throw new Error("--target requires a Rust target triple");
const prepared = prepareManagedPython(targetTriple);
console.log([
  `Managed CPython ${prepared.manifest.pythonVersion} is verified for ${targetTriple}`,
  `provider=${prepared.manifest.provider}`,
  `release=${prepared.manifest.release}`,
  `sha256=${prepared.manifest.archiveSha256}`,
].join(" · "));

