// 发版时四处版本号必须一致：Desktop（package.json）、Tauri（tauri.conf.json）、Rust crate（Cargo.toml）
// 和 Python 包（pyproject.toml，sidecar 就是它打出来的）。pyproject 曾连着几个版本停在 0.2.15 没人发现，
// 所以由 release:verify 把关。只读 [package] / [project] 段里的 version，不碰依赖表里的同名键。
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** TOML 里某个段（如 "project"）下第一行 `version = "x"`；没有返回 null。 */
export function tomlSectionVersion(text, section) {
  let inside = false;
  for (const line of String(text).split(/\r?\n/)) {
    const header = line.match(/^\s*\[([^\]]+)\]\s*$/);
    if (header) {
      inside = header[1].trim() === section;
      continue;
    }
    if (!inside) continue;
    const value = line.match(/^\s*version\s*=\s*"([^"]+)"/);
    if (value) return value[1];
  }
  return null;
}

export function releaseVersions(repoRoot) {
  const desktop = join(repoRoot, "desktop");
  const read = (...parts) => readFileSync(join(...parts), "utf8");
  return {
    "desktop/package.json": JSON.parse(read(desktop, "package.json")).version ?? null,
    "desktop/src-tauri/tauri.conf.json": JSON.parse(read(desktop, "src-tauri", "tauri.conf.json")).version ?? null,
    "desktop/src-tauri/Cargo.toml": tomlSectionVersion(read(desktop, "src-tauri", "Cargo.toml"), "package"),
    "pyproject.toml": tomlSectionVersion(read(repoRoot, "pyproject.toml"), "project"),
  };
}

/** 与 tauri.conf.json（App 实际显示与更新判断用的版本）不一致的文件；全部一致返回空数组。 */
export function versionMismatches(versions) {
  const expected = versions["desktop/src-tauri/tauri.conf.json"];
  return Object.entries(versions)
    .filter(([, version]) => version !== expected)
    .map(([file, version]) => `${file}=${version ?? "missing"} (expected ${expected})`);
}
